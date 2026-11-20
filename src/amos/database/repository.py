"""Repository layer.

Isolates persistence from the domain. The agent does not know a database exists;
it returns an `AgentResult`, and this layer decides how that becomes rows. Swapping
the store, or adding a cache, is a change here and nowhere else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from amos.agents.schemas import AgentResult
from amos.auth import Actor
from amos.database.models import LLMCall, Run, Step, Task, ToolCallRow

# One definition, in the queue module (V1.7). There were two, and they disagreed:
# this module's had EXECUTING, which nothing ever wrote, and no RUNNING.
from amos.worker.queue import RunStatus

__all__ = ["RunRepository", "RunStatus"]


class RunRepository:
    """Reads and writes runs and everything hanging off them.

    **Constructed with its owner** (V1.4, ADR-013). Every query is built through
    `_runs()`, which applies the scope, so there is no method that can be called
    without one — because there is no repository without one.

    That shape was chosen over passing `user_id` into each method, which is one
    forgotten argument away from a leak, and the forgotten version still compiles
    and still returns rows. Isolation fails *silently*: a missing filter does not
    raise, does not log, and returns **more** data rather than less.
    """

    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor

    def _runs(self) -> Select[tuple[Run]]:
        """The only way a query over runs is built.

        `test_no_unscoped_run_query_exists` asserts nothing in this module
        bypasses it, so the guarantee cannot be lost by writing a query the
        ordinary way.
        """
        return select(Run).where(Run.user_id == self._actor.id)

    async def find_by_idempotency_key(self, key: str) -> Run | None:
        """Return an existing run for this key, if any.

        This is what makes a retried HTTP request safe: without it, a client
        timeout followed by a retry silently doubles the work and the cost.

        Scoped, so two users may independently use the same key — theirs is a
        key in *their* namespace, not a global one.
        """
        result = await self._session.execute(
            self._runs().where(Run.idempotency_key == key).options(*_trace_loaders())
        )
        return result.scalar_one_or_none()

    async def create_run(
        self,
        *,
        goal: str,
        request_id: str,
        idempotency_key: str | None = None,
        options: dict[str, bool] | None = None,
        status: str = RunStatus.RECEIVED,
    ) -> Run:
        """Record the run before executing it.

        Written first, deliberately. A run that crashes mid-execution still
        leaves a row saying it was attempted; creating the row afterwards would
        lose exactly the runs most worth investigating.
        """
        run = Run(
            id=uuid.uuid4(),
            user_id=self._actor.id,
            goal_text=goal,
            status=status,
            request_id=request_id,
            idempotency_key=idempotency_key,
            options=options or None,
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

        await self._save_task_rows(run, result)
        await self._add_trace_rows(run, step, result)
        await self._session.flush()
        run.total_tokens = await self._stored_tokens(run, fallback=result.total_tokens)
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
            await self._save_task_rows(run, result)
            await self._add_trace_rows(run, step, result)
        await self._session.flush()
        # A failed run's calls are recorded live since V1.7, so its cost is
        # known even when the agent never returned a result.
        run.total_tokens = await self._stored_tokens(run, fallback=run.total_tokens)
        return run

    async def _has_rows(self, model: type[LLMCall] | type[ToolCallRow], run: Run) -> bool:
        return (
            await self._session.execute(select(model.id).where(model.run_id == run.id).limit(1))
        ).first() is not None

    async def _stored_tokens(self, run: Run, *, fallback: int) -> int:
        """The run's cost as the sum of its stored calls (ADR-015).

        The in-memory total omitted failed task attempts, delegates and earlier
        worker attempts. With no stored calls (no live recording), the
        in-memory figure is all there is.
        """
        total = (
            await self._session.execute(
                select(func.sum(LLMCall.prompt_tokens + LLMCall.output_tokens)).where(
                    LLMCall.run_id == run.id
                )
            )
        ).scalar()
        return int(total) if total is not None else fallback

    async def _save_task_rows(self, run: Run, result: AgentResult) -> None:
        """Persist the task DAG, updating rows that already exist.

        `depends_on` is stored as the planner's symbolic refs resolved to the
        UUIDs of the rows created here, so the stored graph is self-contained
        and does not depend on the plan text surviving.

        **Why this updates rather than inserts.** Since V1.1 the plan is written
        before execution and each task is checkpointed as it finishes (ADR-010),
        so by the time a run completes its rows usually already exist — and
        `UNIQUE (run_id, plan_ref)` would reject a second insert. Rows are
        upserted rather than the write being skipped, so this stays the backstop
        that reconciles final state when a checkpoint failed partway through.
        """
        if not result.tasks:
            return

        existing = {
            row.plan_ref: row
            for row in (await self._session.execute(select(Task).where(Task.run_id == run.id)))
            .scalars()
            .all()
        }

        ids = {
            record.plan_ref: existing[record.plan_ref].id
            if record.plan_ref in existing
            else uuid.uuid4()
            for record in result.tasks
        }

        for record in result.tasks:
            row = existing.get(record.plan_ref)
            if row is None:
                row = Task(
                    id=ids[record.plan_ref],
                    run_id=run.id,
                    plan_ref=record.plan_ref,
                    description=record.description,
                    depends_on=[ids[ref] for ref in record.depends_on if ref in ids],
                    position=record.position,
                )
                self._session.add(row)
            row.state = record.state
            row.attempt_count = record.attempt_count
            row.result = {"answer": record.answer} if record.answer else None
            row.error = {"message": record.error} if record.error else None

    async def _add_trace_rows(self, run: Run, step: Step, result: AgentResult) -> None:
        """Turn the agent's in-memory records into rows — unless they were
        already recorded live (ADR-015), in which case writing them again would
        double every call.

        Decided per table and per run, not by a global switch: agents that do
        not record live (test doubles, an agent built without `build_provider`)
        still get their rows this way.
        """
        live_llm = await self._has_rows(LLMCall, run)
        live_tools = await self._has_rows(ToolCallRow, run)
        for record in [] if live_llm else result.llm_calls:
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
        for outcome in [] if live_tools else result.tool_outcomes:
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
            self._runs().where(Run.id == run_id).options(*_trace_loaders())
        )
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 20, before: datetime | None = None) -> list[Run]:
        """The actor's runs, newest first; `before` pages backwards."""
        query = self._runs().order_by(Run.created_at.desc()).limit(limit)
        if before is not None:
            query = query.where(Run.created_at < before)
        result = await self._session.execute(query)
        return list(result.scalars().all())


def _trace_loaders() -> tuple[Any, ...]:
    return (
        selectinload(Run.tasks),
        selectinload(Run.steps),
        selectinload(Run.llm_calls),
        selectinload(Run.tool_calls),
        selectinload(Run.events),
    )
