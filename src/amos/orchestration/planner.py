"""Planner: goal → validated task DAG.

The planner is the one place an LLM decides *structure*. Everything it produces
is checked before it can affect anything: Pydantic checks the shape, `Plan`'s
validator checks that ids are unique, dependencies resolve, and the graph is
acyclic. Only then does a row get written.

Order matters. A cyclic plan that reached the database would be a run that can
never complete, holding rows that look live forever.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from amos.errors import AmosError
from amos.llm.base import LLMCallRecord, LLMProvider, LLMRequest
from amos.observability import log_event
from amos.orchestration.plan import MAX_TASKS, InvalidPlanError, Plan

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_INSTRUCTION = f"""You are AMOS's planner. Decompose a goal into tasks.

Rules:
- Produce the FEWEST tasks that actually do the job. One task is correct for a
  simple goal; do not invent structure to look thorough.
- At most {MAX_TASKS} tasks.
- Each description must be self-contained. The executor runs one task at a time
  and cannot see the others, so "summarise the above" is useless — say what to
  summarise.
- Use depends_on ONLY when a task genuinely needs an earlier task's output.
  Independent tasks should have no dependencies so they can run concurrently.
- Task ids are short symbols: t1, t2, t3.
- Never create a cycle. Dependencies must point backwards only.
"""

REPAIR_SUFFIX = """

Your previous plan was rejected:
{error}

Produce a corrected plan."""


class PlanningError(AmosError):
    """The planner could not produce a valid plan within its attempts."""


class Planner:
    """Turns a goal into a validated `Plan`."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        timeout: float = 60.0,
        max_attempts: int = 2,
        temperature: float = 0.1,
    ) -> None:
        self._provider = provider
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._temperature = temperature

    async def plan(self, goal: str, calls: list[LLMCallRecord] | None = None) -> Plan:
        last_error = ""

        for attempt in range(self._max_attempts):
            instruction = PLANNER_SYSTEM_INSTRUCTION
            if attempt > 0:
                instruction += REPAIR_SUFFIX.format(error=last_error)

            response = await self._provider.complete(
                LLMRequest(
                    prompt=f"Goal: {goal}",
                    system_instruction=instruction,
                    response_schema=Plan,
                    temperature=self._temperature,
                ),
                timeout=self._timeout,
            )
            if calls is not None:
                calls.append(
                    LLMCallRecord(
                        provider=response.provider,
                        model=response.model,
                        prompt_tokens=response.prompt_tokens,
                        output_tokens=response.output_tokens,
                        latency_ms=response.latency_ms,
                        repair_attempt=attempt,
                    )
                )

            plan, error = _validate(response.parsed, response.text)
            if plan is not None:
                log_event(
                    logger,
                    "planner.produced",
                    tasks=len(plan.tasks),
                    attempt=attempt,
                    dependencies=sum(len(t.depends_on) for t in plan.tasks),
                )
                return plan

            last_error = error
            log_event(logger, "planner.rejected", attempt=attempt, error=error)

        raise PlanningError(
            f"Planner failed to produce a valid plan in {self._max_attempts} attempts",
            details={"last_error": last_error},
        )


def _validate(parsed: object | None, raw_text: str) -> tuple[Plan | None, str]:
    """Validate planner output. Never raises — the caller decides whether to retry."""
    if isinstance(parsed, Plan):
        return parsed, ""
    try:
        if parsed is not None and hasattr(parsed, "model_dump"):
            return Plan.model_validate(parsed.model_dump()), ""
        if not raw_text.strip():
            return None, "Planner returned an empty response"
        return Plan.model_validate_json(raw_text), ""
    except (ValidationError, InvalidPlanError, ValueError) as exc:
        return None, str(exc)[:500]
