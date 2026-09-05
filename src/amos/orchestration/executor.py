"""Executor: walks the task DAG deterministically.

This module contains **no LLM calls of its own**. It decides what may run, what
must be retried, what must be skipped, and when a run is over. The agent it
delegates to does the reasoning; the executor owns the guarantees.

That split is the whole point. If the model could decide a task had succeeded,
or grant itself another retry, none of the properties below would hold:

- a task's state only ever changes through `assert_transition`
- retries are bounded and backed off
- a dependency that can never succeed skips its dependents, transitively
- the run ends, always — every task reaches a terminal state
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

from amos.agents.schemas import AgentResult
from amos.errors import AmosError
from amos.llm.base import LLMCallRecord
from amos.observability import log_event
from amos.orchestration.plan import Plan, PlannedTask
from amos.orchestration.retry import backoff_delay, should_retry
from amos.orchestration.state import (
    UNRECOVERABLE_STATES,
    TaskState,
    assert_transition,
    is_terminal,
)
from amos.tools.base import ToolOutcome

logger = logging.getLogger(__name__)


class TaskRunner(Protocol):
    """What the executor needs from an agent. Deliberately narrow."""

    async def run(self, goal: str) -> AgentResult: ...


@dataclass
class TaskExecution:
    """A task's in-memory state during a run."""

    plan_ref: str
    description: str
    depends_on: list[str]
    position: int
    max_attempts: int = 3
    state: TaskState = TaskState.PENDING
    attempt_count: int = 0
    result: AgentResult | None = None
    error: str | None = None
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    tool_outcomes: list[ToolOutcome] = field(default_factory=list)

    def transition(self, target: TaskState) -> None:
        """The only way a task's state changes."""
        self.state = assert_transition(self.state, target)


class RunOutcome:
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


@dataclass
class ExecutionReport:
    outcome: str
    tasks: list[TaskExecution]

    @property
    def succeeded(self) -> list[TaskExecution]:
        return [t for t in self.tasks if t.state is TaskState.SUCCEEDED]

    @property
    def total_tokens(self) -> int:
        return sum(c.prompt_tokens + c.output_tokens for t in self.tasks for c in t.llm_calls)

    @property
    def all_llm_calls(self) -> list[LLMCallRecord]:
        return [c for t in self.tasks for c in t.llm_calls]

    @property
    def all_tool_outcomes(self) -> list[ToolOutcome]:
        return [o for t in self.tasks for o in t.tool_outcomes]


class Executor:
    """Runs a validated plan to completion."""

    def __init__(
        self,
        runner: TaskRunner,
        *,
        max_attempts: int = 3,
        sleep: object | None = None,
    ) -> None:
        self._runner = runner
        self._max_attempts = max_attempts
        # Injectable so retry tests do not actually wait for backoff.
        self._sleep = sleep or asyncio.sleep

    async def execute(self, plan: Plan) -> ExecutionReport:
        tasks = {
            task.id: TaskExecution(
                plan_ref=task.id,
                description=task.description,
                depends_on=list(task.depends_on),
                position=index,
                max_attempts=self._max_attempts,
            )
            for index, task in enumerate(plan.topological_order())
        }

        # The loop terminates because every iteration either moves at least one
        # task towards a terminal state, or finds nothing runnable and stops.
        while True:
            self._skip_unreachable(tasks)
            self._promote_ready(tasks)

            runnable = [t for t in tasks.values() if t.state is TaskState.READY]
            if not runnable:
                break

            # Independent ready tasks run concurrently. This is where the DAG
            # earns its keep over a list: tasks with no dependency on each other
            # do not wait for each other.
            await asyncio.gather(*(self._run_task(t, tasks) for t in runnable))

        return ExecutionReport(outcome=self._classify(tasks), tasks=list(tasks.values()))

    def _promote_ready(self, tasks: dict[str, TaskExecution]) -> None:
        """PENDING → READY once every dependency has succeeded."""
        for task in tasks.values():
            if task.state is not TaskState.PENDING:
                continue
            if all(tasks[dep].state is TaskState.SUCCEEDED for dep in task.depends_on):
                task.transition(TaskState.READY)

    def _skip_unreachable(self, tasks: dict[str, TaskExecution]) -> None:
        """Skip tasks whose dependencies can never succeed.

        Repeats until stable, because skipping propagates: if t2 depends on a
        failed t1, and t3 depends on t2, then t3 must be skipped too. Doing this
        in one pass would leave t3 waiting forever on a dependency that will
        never move.
        """
        changed = True
        while changed:
            changed = False
            for task in tasks.values():
                if task.state in (TaskState.PENDING, TaskState.READY) and any(
                    tasks[dep].state in UNRECOVERABLE_STATES for dep in task.depends_on
                ):
                    blocker = next(
                        dep for dep in task.depends_on if tasks[dep].state in UNRECOVERABLE_STATES
                    )
                    task.error = f"Skipped: dependency '{blocker}' did not succeed"
                    task.transition(TaskState.SKIPPED)
                    log_event(logger, "task.skipped", task=task.plan_ref, blocked_by=blocker)
                    changed = True

    async def _run_task(self, task: TaskExecution, tasks: dict[str, TaskExecution]) -> None:
        task.transition(TaskState.RUNNING)
        task.attempt_count += 1

        try:
            result = await self._runner.run(self._build_goal(task, tasks))
        except AmosError as exc:
            task.error = f"{type(exc).__name__}: {exc.message}"
            task.transition(TaskState.FAILED)
            log_event(
                logger,
                "task.failed",
                task=task.plan_ref,
                attempt=task.attempt_count,
                error=type(exc).__name__,
            )
            await self._handle_failure(task)
            return

        task.result = result
        task.llm_calls.extend(result.llm_calls)
        task.tool_outcomes.extend(result.tool_outcomes)
        task.transition(TaskState.SUCCEEDED)
        log_event(logger, "task.succeeded", task=task.plan_ref, attempt=task.attempt_count)

    async def _handle_failure(self, task: TaskExecution) -> None:
        """Retry with backoff, or give up permanently.

        A retry returns the task to READY — the normal path — rather than a
        special retry state. One code path for "about to run" means retried
        tasks cannot behave differently from first attempts.
        """
        if should_retry(task.attempt_count, task.max_attempts):
            delay = backoff_delay(task.attempt_count - 1)
            log_event(
                logger,
                "task.retrying",
                task=task.plan_ref,
                attempt=task.attempt_count,
                delay_seconds=round(delay, 3),
            )
            await self._sleep(delay)  # type: ignore[operator]
            task.transition(TaskState.READY)
        else:
            task.transition(TaskState.PERMANENTLY_FAILED)
            log_event(
                logger,
                "task.permanently_failed",
                task=task.plan_ref,
                attempts=task.attempt_count,
            )

    @staticmethod
    def _build_goal(task: TaskExecution, tasks: dict[str, TaskExecution]) -> str:
        """The task description, plus results of anything it depends on.

        Dependency results are passed as context because each task description
        is written to be self-contained — the executor does not assume the agent
        remembers anything between tasks.
        """
        if not task.depends_on:
            return task.description

        context = []
        for dep in task.depends_on:
            upstream = tasks[dep]
            if upstream.result is not None:
                context.append(
                    f"- {upstream.description}\n  Result: {upstream.result.response.answer}"
                )

        if not context:
            return task.description
        joined = "\n".join(context)
        return f"{task.description}\n\nResults of earlier steps you may use:\n{joined}"

    @staticmethod
    def _classify(tasks: dict[str, TaskExecution]) -> str:
        """Decide the run's outcome.

        `PARTIALLY_COMPLETED` exists deliberately. A research goal where three
        sources answered and one timed out produced real value; forcing that
        into binary success/failure would either discard good work or overstate
        what happened.
        """
        states = [t.state for t in tasks.values()]
        assert all(is_terminal(s) for s in states), "executor left a task non-terminal"

        succeeded = sum(1 for s in states if s is TaskState.SUCCEEDED)
        if succeeded == len(states):
            return RunOutcome.COMPLETED
        if succeeded == 0:
            return RunOutcome.FAILED
        return RunOutcome.PARTIALLY_COMPLETED


def plan_task_count(plan: Plan) -> int:
    return len(plan.tasks)


__all__ = [
    "ExecutionReport",
    "Executor",
    "PlannedTask",
    "RunOutcome",
    "TaskExecution",
    "TaskRunner",
]
