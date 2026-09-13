"""Durable progress for a run in flight (V1.1, ADR-010).

Until V1.1 nothing about a run was written until it finished: tasks, steps, LLM
calls and tool calls all landed in one batch in `RunRepository.record_success`.
A run killed mid-execution therefore left a `runs` row and no evidence of what it
had already done, which is why a reclaimed run could only be re-executed from the
beginning.

This module is the other half. It implements two protocols the orchestration
layer defines and does not depend on a database for:

- `PlanStore` (`orchestration/orchestrator.py`) — persist the plan when it is
  made, and hand it back on a later attempt instead of re-planning.
- `TaskCheckpoint` (`orchestration/executor.py`) — record each task as it reaches
  a terminal state.

Both read the run id from the request context rather than taking it as an
argument: the orchestrator and executor are built once at startup, the same
reason the memory tools read it (`memory/tools.py`).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from amos.database.engine import session_scope
from amos.database.models import Task
from amos.observability import get_current_run_id, log_event
from amos.orchestration.plan import Plan, PlannedTask
from amos.orchestration.state import TaskState

if TYPE_CHECKING:
    from amos.orchestration.executor import TaskExecution

logger = logging.getLogger(__name__)


class RunProgress:
    """Reads and writes a run's plan and task states, keyed by the current run."""

    def __init__(self, session_factory: Any) -> None:
        self._factory = session_factory

    @staticmethod
    def _run_id() -> uuid.UUID | None:
        raw = get_current_run_id()
        if not raw:
            return None
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None

    # --- PlanStore ---------------------------------------------------------

    async def load(self) -> tuple[Plan, dict[str, str]] | None:
        """The stored plan for this run, with whatever already succeeded.

        Returns `None` when the run has no task rows — the normal first-attempt
        case — so the caller plans as usual.
        """
        run_id = self._run_id()
        if run_id is None:
            return None

        async with session_scope(self._factory) as session:
            rows = list(
                (
                    await session.execute(
                        select(Task).where(Task.run_id == run_id).order_by(Task.position)
                    )
                )
                .scalars()
                .all()
            )

        if not rows:
            return None

        # depends_on is stored as row UUIDs so the graph survives without the
        # plan text (V0.4). Rebuilding the Plan means mapping them back to the
        # planner's symbolic refs.
        ref_by_id = {row.id: row.plan_ref for row in rows}
        plan = Plan(
            reasoning="Restored from stored tasks; this run was planned on an earlier attempt.",
            tasks=[
                PlannedTask(
                    id=row.plan_ref,
                    description=row.description,
                    depends_on=[ref_by_id[dep] for dep in row.depends_on if dep in ref_by_id],
                )
                for row in rows
            ],
        )

        completed = {
            row.plan_ref: str((row.result or {}).get("answer", ""))
            for row in rows
            if row.state == TaskState.SUCCEEDED.value and (row.result or {}).get("answer")
        }
        return plan, completed

    async def save(self, plan: Plan) -> None:
        """Write the plan's tasks before any of them runs.

        Idempotent by construction: if rows already exist for this run, `load`
        would have returned them and the planner would not have been called.
        """
        run_id = self._run_id()
        if run_id is None:
            return

        ids = {task.id: uuid.uuid4() for task in plan.tasks}
        async with session_scope(self._factory) as session:
            for position, task in enumerate(plan.topological_order()):
                session.add(
                    Task(
                        id=ids[task.id],
                        run_id=run_id,
                        plan_ref=task.id,
                        description=task.description,
                        state=TaskState.PENDING.value,
                        depends_on=[ids[ref] for ref in task.depends_on if ref in ids],
                        position=position,
                    )
                )
        log_event(logger, "plan.persisted", run_id=str(run_id), tasks=len(plan.tasks))

    # --- TaskCheckpoint ----------------------------------------------------

    async def record(self, task: TaskExecution) -> None:
        """Update one task row to its terminal state.

        May raise; the executor catches and logs. Losing a checkpoint costs
        resumability for one task and must not fail a run that is succeeding.
        """
        run_id = self._run_id()
        if run_id is None:
            return

        answer = task.result.response.answer if task.result is not None else None
        async with session_scope(self._factory) as session:
            row = (
                await session.execute(
                    select(Task).where(Task.run_id == run_id, Task.plan_ref == task.plan_ref)
                )
            ).scalar_one_or_none()
            if row is None:
                # No row to update: the plan was never persisted (persistence
                # disabled, or this run predates V1.1). Not an error.
                return
            row.state = task.state.value
            row.attempt_count = task.attempt_count
            row.result = {"answer": answer} if answer else None
            row.error = {"message": task.error} if task.error else None
