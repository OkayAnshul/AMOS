"""Repository layer.

Isolates persistence from the domain. The agent does not know a database exists;
it returns an `AgentResult`, and this layer decides how that becomes rows. Swapping
the store, or adding a cache, is a change here and nowhere else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from amos.agents.schemas import AgentResult
from amos.database.models import LLMCall, Run, Step, Task, ToolCallRow


class RunStatus:
    RECEIVED = "RECEIVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


class RunRepository:
    """Reads and writes runs and everything hanging off them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_idempotency_key(self, key: str) -> Run | None:
        """Return an existing run for this key, if any.

        This is what makes a retried HTTP request safe: without it, a client
        timeout followed by a retry silently doubles the work and the cost.
        """
        result = await self._session.execute(
            select(Run).where(Run.idempotency_key == key).options(*_trace_loaders())
        )
        return result.scalar_one_or_none()

    async def create_run(
        self,
        *,
        goal: str,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> Run:
        """Record the run before executing it.

        Written first, deliberately. A run that crashes mid-execution still
        leaves a row saying it was attempted; creating the row afterwards would
        lose exactly the runs most worth investigating.
        """
        run = Run(
            id=uuid.uuid4(),
            goal_text=goal,
            status=RunStatus.RECEIVED,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def record_success(self, run: Run, result: AgentResult) -> Run:
        """Persist a completed run and its full trace."""
        # The run's status mirrors the executor's verdict, so a run where three
        # of four tasks succeeded is not recorded as an unqualified success.
        run.status = result.outcome or RunStatus.COMPLETED
        run.result = result.response.model_dump(mode="json")
        run.total_tokens = result.total_tokens
        run.latency_ms = result.latency_ms
        run.completed_at = datetime.now(UTC)

        step = Step(
            id=uuid.uuid4(),
            run_id=run.id,
            attempt=0,
            status="SUCCEEDED",
            agent_name="tool_using_agent",
            input={"goal": run.goal_text},
            output=result.response.model_dump(mode="json"),
            finished_at=datetime.now(UTC),
        )
        self._session.add(step)
        await self._session.flush()

        self._add_task_rows(run, result)
        self._add_trace_rows(run, step, result)
        await self._session.flush()
        return run

    async def record_failure(
        self, run: Run, error_type: str, message: str, result: AgentResult | None = None
    ) -> Run:
        """Persist a failed run — including whatever work it did first.

        The partial trace is the point: a failed run that consumed tokens and
        called tools should show that, not vanish.
        """
        run.status = RunStatus.FAILED
        run.error = {"type": error_type, "message": message}
        run.completed_at = datetime.now(UTC)

        step = Step(
            id=uuid.uuid4(),
            run_id=run.id,
            attempt=0,
            status="FAILED",
            agent_name="tool_using_agent",
            input={"goal": run.goal_text},
            error={"type": error_type, "message": message},
            finished_at=datetime.now(UTC),
        )
        self._session.add(step)
        await self._session.flush()

        if result is not None:
            run.total_tokens = result.total_tokens
            run.latency_ms = result.latency_ms
            self._add_task_rows(run, result)
            self._add_trace_rows(run, step, result)
        await self._session.flush()
        return run

    def _add_task_rows(self, run: Run, result: AgentResult) -> None:
        """Persist the task DAG.

        `depends_on` is stored as the planner's symbolic refs resolved to the
        UUIDs of the rows created here, so the stored graph is self-contained
        and does not depend on the plan text surviving.
        """
        if not result.tasks:
            return

        ids = {record.plan_ref: uuid.uuid4() for record in result.tasks}
        for record in result.tasks:
            self._session.add(
                Task(
                    id=ids[record.plan_ref],
                    run_id=run.id,
                    plan_ref=record.plan_ref,
                    description=record.description,
                    state=record.state,
                    depends_on=[ids[ref] for ref in record.depends_on if ref in ids],
                    position=record.position,
                    attempt_count=record.attempt_count,
                    result={"answer": record.answer} if record.answer else None,
                    error={"message": record.error} if record.error else None,
                )
            )

    def _add_trace_rows(self, run: Run, step: Step, result: AgentResult) -> None:
        """Turn the agent's in-memory records into rows.

        Note how mechanical this is — that is the V0.1/V0.2 seams paying off.
        """
        for record in result.llm_calls:
            self._session.add(
                LLMCall(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    step_id=step.id,
                    provider=record.provider,
                    model=record.model,
                    prompt_tokens=record.prompt_tokens,
                    output_tokens=record.output_tokens,
                    latency_ms=record.latency_ms,
                    repair_attempt=record.repair_attempt,
                    error=record.error,
                )
            )
        for outcome in result.tool_outcomes:
            self._session.add(
                ToolCallRow(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    step_id=step.id,
                    call_id=outcome.call_id,
                    tool_name=outcome.name,
                    arguments=outcome.arguments,
                    output=outcome.output,
                    status=outcome.status.value,
                    error=outcome.error,
                    latency_ms=outcome.latency_ms,
                )
            )

    async def get_trace(self, run_id: uuid.UUID) -> Run | None:
        """Load a run with its whole trace in one round trip.

        `selectinload` rather than lazy loading: lazy attribute access on an
        async session raises, and even if it did not, it would be N+1 queries
        for the single most common read in the system.
        """
        result = await self._session.execute(
            select(Run).where(Run.id == run_id).options(*_trace_loaders())
        )
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 20) -> list[Run]:
        result = await self._session.execute(
            select(Run).order_by(Run.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())


def _trace_loaders() -> tuple[Any, ...]:
    return (
        selectinload(Run.tasks),
        selectinload(Run.steps),
        selectinload(Run.llm_calls),
        selectinload(Run.tool_calls),
    )
