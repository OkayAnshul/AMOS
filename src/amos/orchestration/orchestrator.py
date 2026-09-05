"""Orchestrator: goal → plan → execution → answer.

Composes the planner and the executor and presents the same
`run(goal) -> AgentResult` interface as the single-shot agents. That is
deliberate: `RunService` persists any of them without knowing which ran, so
adding orchestration did not require changing the persistence layer.

Cost note, which shaped the design: on a 20-requests-per-day free tier, a
three-task plan already costs 1 planning call + 3x2 execution calls. The
synthesis step is therefore **skipped entirely when a single task ran** — its
answer is already the answer, and spending a call to restate it would be waste
with no benefit.
"""

from __future__ import annotations

import logging
import time

from amos.agents.schemas import AgentResponse, AgentResult, Confidence, TaskRecord
from amos.llm.base import LLMCallRecord, LLMProvider, LLMRequest
from amos.observability import get_request_id, log_event, new_request_id
from amos.orchestration.executor import (
    ExecutionReport,
    Executor,
    RunOutcome,
    TaskExecution,
    TaskRunner,
)
from amos.orchestration.planner import Planner
from amos.orchestration.state import TaskState

logger = logging.getLogger(__name__)

SYNTHESIS_INSTRUCTION = """You are AMOS. Several tasks were carried out to answer a goal.

Combine their results into one coherent answer to the original goal.
- Use only what the task results actually say. Do not add facts.
- If some tasks failed, answer from what succeeded and record the gap in caveats.
- Set confidence honestly: partial information means lower confidence.
"""


class Orchestrator:
    """Plans a goal, executes the plan, and synthesises the result."""

    def __init__(
        self,
        provider: LLMProvider,
        runner: TaskRunner,
        *,
        timeout: float = 60.0,
        max_attempts: int = 3,
        temperature: float = 0.2,
        planner: Planner | None = None,
        executor: Executor | None = None,
    ) -> None:
        self._provider = provider
        self._runner = runner
        self._timeout = timeout
        self._temperature = temperature
        self._planner = planner or Planner(provider, timeout=timeout)
        self._executor = executor or Executor(runner, max_attempts=max_attempts)

    @property
    def tool_names(self) -> list[str]:
        return (
            getattr(self._executor, "_runner", None)
            and getattr(self._executor._runner, "tool_names", [])
            or []
        )

    async def run(self, goal: str) -> AgentResult:
        request_id = get_request_id() or new_request_id()
        started = time.perf_counter()
        calls: list[LLMCallRecord] = []

        plan = await self._planner.plan(goal, calls)
        report = await self._executor.execute(plan)
        calls.extend(report.all_llm_calls)

        response = await self._synthesise(goal, report, calls)
        total_ms = int((time.perf_counter() - started) * 1000)

        log_event(
            logger,
            "orchestrator.completed",
            outcome=report.outcome,
            tasks=len(report.tasks),
            succeeded=len(report.succeeded),
            llm_calls=len(calls),
            latency_ms=total_ms,
        )

        return AgentResult(
            request_id=request_id,
            response=response,
            llm_calls=calls,
            tool_outcomes=report.all_tool_outcomes,
            tasks=[_to_record(t) for t in report.tasks],
            outcome=report.outcome,
            total_tokens=sum(c.prompt_tokens + c.output_tokens for c in calls),
            latency_ms=total_ms,
        )

    async def _synthesise(
        self, goal: str, report: ExecutionReport, calls: list[LLMCallRecord]
    ) -> AgentResponse:
        succeeded = report.succeeded

        if not succeeded:
            # Nothing to synthesise, and no call worth spending.
            failures = "; ".join(f"{t.plan_ref}: {t.error}" for t in report.tasks if t.error)
            return AgentResponse(
                answer="Could not complete this goal.",
                reasoning="Every task failed or was skipped.",
                assumptions=[],
                confidence=Confidence.LOW,
                caveats=[failures or "All tasks failed."],
            )

        if len(succeeded) == 1 and len(report.tasks) == 1:
            # One task answered the whole goal. Its answer IS the answer;
            # paying for a call to rephrase it would be pure waste.
            single = succeeded[0]
            assert single.result is not None
            return single.result.response

        findings = "\n\n".join(
            f"Task {t.plan_ref}: {t.description}\nResult: {t.result.response.answer}"
            for t in succeeded
            if t.result is not None
        )
        skipped = [t.plan_ref for t in report.tasks if t.state is not TaskState.SUCCEEDED]
        prompt = f"Original goal: {goal}\n\nTask results:\n{findings}" + (
            f"\n\nThese tasks did not succeed: {', '.join(skipped)}" if skipped else ""
        )

        response = await self._provider.complete(
            LLMRequest(
                prompt=prompt,
                system_instruction=SYNTHESIS_INSTRUCTION,
                response_schema=AgentResponse,
                temperature=self._temperature,
            ),
            timeout=self._timeout,
        )
        calls.append(
            LLMCallRecord(
                provider=response.provider,
                model=response.model,
                prompt_tokens=response.prompt_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
            )
        )

        if isinstance(response.parsed, AgentResponse):
            answer = response.parsed
        else:
            answer = AgentResponse(
                answer=response.text or findings,
                reasoning="Synthesis did not validate; returning combined task results.",
                assumptions=[],
                confidence=Confidence.LOW,
                caveats=["Synthesis output failed schema validation."],
            )

        if report.outcome == RunOutcome.PARTIALLY_COMPLETED:
            answer.caveats.append(
                f"Partial result: {len(succeeded)} of {len(report.tasks)} tasks succeeded."
            )
        return answer


def _to_record(task: TaskExecution) -> TaskRecord:
    return TaskRecord(
        plan_ref=task.plan_ref,
        description=task.description,
        state=task.state.value,
        depends_on=list(task.depends_on),
        attempt_count=task.attempt_count,
        position=task.position,
        answer=task.result.response.answer if task.result is not None else None,
        error=task.error,
    )
