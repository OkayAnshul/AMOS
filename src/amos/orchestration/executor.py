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

from amos.agents.findings import check_findings
from amos.agents.schemas import (
    AgentResponse,
    AgentResult,
    CheckedFinding,
    Confidence,
    FindingStatus,
)
from amos.agents.sourcing import sources_in
from amos.database.recorder import record_event
from amos.errors import (
    AmosError,
    BudgetExhaustedError,
    ProviderAuthError,
    ProviderRateLimitError,
)
from amos.llm.base import LLMCallRecord
from amos.observability import call_scope, log_event
from amos.orchestration.plan import Plan, PlannedTask
from amos.orchestration.retry import backoff_delay, should_retry
from amos.orchestration.state import (
    UNRECOVERABLE_STATES,
    TaskState,
    assert_transition,
    is_terminal,
)
from amos.telemetry.metrics import instruments, safe_labels
from amos.tools.base import ToolOutcome

logger = logging.getLogger(__name__)


class TaskRunner(Protocol):
    """What the executor needs from an agent. Deliberately narrow."""

    async def run(self, goal: str) -> AgentResult: ...


class TaskCheckpoint(Protocol):
    """Where a task's outcome is durably recorded, if anywhere.

    The executor has no database access and does not acquire any — it depends on
    this the same way it depends on `TaskRunner`, and the persistence layer
    supplies the implementation (ADR-010). Tests pass a fake, or nothing.

    Called on every terminal transition. Implementations **must not raise**: a
    checkpoint that fails costs resumability for one task, and must never fail a
    run that is otherwise succeeding.
    """

    async def record(self, task: TaskExecution) -> None: ...


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


#: Wall time for one task attempt. Above the worst case of the bounds beneath it
#: (agent iterations x (LLM timeout + tool timeout)), below the worker's
#: visibility timeout — see ADR-009.
DEFAULT_TASK_TIMEOUT_SECONDS = 300.0


class Executor:
    """Runs a validated plan to completion."""

    def __init__(
        self,
        runner: TaskRunner,
        *,
        max_attempts: int = 3,
        task_timeout_seconds: float = DEFAULT_TASK_TIMEOUT_SECONDS,
        checkpoint: TaskCheckpoint | None = None,
        sleep: object | None = None,
    ) -> None:
        self._runner = runner
        self._max_attempts = max_attempts
        self._task_timeout = task_timeout_seconds
        self._checkpoint = checkpoint
        # Injectable so retry tests do not actually wait for backoff.
        self._sleep = sleep or asyncio.sleep

    async def execute(
        self, plan: Plan, *, completed: dict[str, str] | None = None
    ) -> ExecutionReport:
        """Run a plan to completion.

        `completed` maps a task's `plan_ref` to the answer a *previous* attempt
        at this run already produced (ADR-010). Those tasks are marked SUCCEEDED
        without being run, and their answers still reach their dependents — which
        is the whole point of resuming rather than re-executing.
        """
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

        for plan_ref, answer in (completed or {}).items():
            task = tasks.get(plan_ref)
            if task is None:
                # The stored row names a task this plan does not contain. Under
                # ADR-010 the stored rows *are* the plan, so this should be
                # impossible; if it happens, redoing the work is the safe answer.
                log_event(logger, "task.resume_ref_unknown", task=plan_ref)
                continue
            # Straight to SUCCEEDED via the same transition table as everything
            # else — PENDING -> READY -> RUNNING -> SUCCEEDED. Resuming does not
            # get its own path into a state.
            task.transition(TaskState.READY)
            task.transition(TaskState.RUNNING)
            task.result = _resumed_result(answer)
            task.transition(TaskState.SUCCEEDED)
            log_event(logger, "task.resumed", task=plan_ref)
            with call_scope(task_ref=plan_ref):
                await record_event("task.resumed")

        # The loop terminates because every iteration either moves at least one
        # task towards a terminal state, or finds nothing runnable and stops.
        while True:
            for task in self._skip_unreachable(tasks):
                await self._save(task)
                with call_scope(task_ref=task.plan_ref):
                    await record_event("task.skipped", reason=task.error)
            self._promote_ready(tasks)

            runnable = [t for t in tasks.values() if t.state is TaskState.READY]
            if not runnable:
                break

            # Independent ready tasks run concurrently. This is where the DAG
            # earns its keep over a list: tasks with no dependency on each other
            # do not wait for each other.
            await asyncio.gather(*(self._run_task(t, tasks) for t in runnable))

        return ExecutionReport(outcome=self._classify(tasks), tasks=list(tasks.values()))

    async def _save(self, task: TaskExecution) -> None:
        """Checkpoint a task, never letting the checkpoint fail the run.

        Same reasoning as episodic recording in `api/persistence.py`: losing the
        ability to resume one task is a smaller harm than failing a run whose
        work is already done and correct.
        """
        if self._checkpoint is None:
            return
        try:
            await self._checkpoint.record(task)
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger,
                "task.checkpoint_failed",
                task=task.plan_ref,
                state=task.state.value,
                error=type(exc).__name__,
            )

    def _promote_ready(self, tasks: dict[str, TaskExecution]) -> None:
        """PENDING → READY once its dependencies allow it.

        One dependency: it must have succeeded. Two or more — a task that
        combines results — all must have finished and at least one succeeded
        (ADR-023): two good research tasks are worth a plan that says what the
        third would have added; skipping it threw that work away.
        """
        for task in tasks.values():
            if task.state is not TaskState.PENDING:
                continue
            states = [tasks[dep].state for dep in task.depends_on]
            every_input = all(s is TaskState.SUCCEEDED for s in states)
            enough_inputs = (
                len(states) >= 2
                and all(s is TaskState.SUCCEEDED or s in UNRECOVERABLE_STATES for s in states)
                and any(s is TaskState.SUCCEEDED for s in states)
            )
            if every_input or enough_inputs:
                task.transition(TaskState.READY)

    def _skip_unreachable(self, tasks: dict[str, TaskExecution]) -> list[TaskExecution]:
        """Skip tasks whose dependencies can never succeed.

        Repeats until stable, because skipping propagates: if t2 depends on a
        failed t1, and t3 depends on t2, then t3 must be skipped too. Doing this
        in one pass would leave t3 waiting forever on a dependency that will
        never move.
        """
        skipped: list[TaskExecution] = []
        changed = True
        while changed:
            changed = False
            for task in tasks.values():
                failed = [d for d in task.depends_on if tasks[d].state in UNRECOVERABLE_STATES]
                # A combining task is unreachable only when none of its inputs can
                # arrive (ADR-023); a single-input task, as soon as its input fails.
                unreachable = bool(failed) and (
                    len(task.depends_on) < 2 or len(failed) == len(task.depends_on)
                )
                if task.state in (TaskState.PENDING, TaskState.READY) and unreachable:
                    blocker = next(
                        dep for dep in task.depends_on if tasks[dep].state in UNRECOVERABLE_STATES
                    )
                    task.error = f"Skipped: dependency '{blocker}' did not succeed"
                    task.transition(TaskState.SKIPPED)
                    log_event(logger, "task.skipped", task=task.plan_ref, blocked_by=blocker)
                    skipped.append(task)
                    changed = True
        return skipped

    async def _run_task(self, task: TaskExecution, tasks: dict[str, TaskExecution]) -> None:
        # The scope opens INSIDE the gathered coroutine: `gather` copies context
        # per child, so each concurrent task keeps its own task_ref (ADR-015).
        with call_scope(
            task_ref=task.plan_ref,
            task_attempt=task.attempt_count + 1,
            purpose="agent",
            agent=None,
        ):
            await self._attempt(task, tasks)

    async def _attempt(self, task: TaskExecution, tasks: dict[str, TaskExecution]) -> None:
        missing = [d for d in task.depends_on if tasks[d].state is not TaskState.SUCCEEDED]
        if missing and task.attempt_count == 0:
            await record_event("task.partial_inputs", missing=missing)
        task.transition(TaskState.RUNNING)
        task.attempt_count += 1
        # Written live since V1.7, so a running task does not look PENDING.
        # Resume trusts only SUCCEEDED rows, so an interim state is safe to store.
        await self._save(task)
        await record_event("task.started", description=task.description)

        try:
            # The outermost bound. Everything underneath has its own timeout —
            # each LLM call, each tool call — but a task is a *loop* over those,
            # so bounded parts do not add up to a bounded whole. Without this,
            # TaskState.TIMED_OUT was declared with legal transitions in and out
            # and was unreachable: nothing could ever produce it.
            result = await asyncio.wait_for(
                self._runner.run(self._build_goal(task, tasks)),
                timeout=self._task_timeout,
            )
        except TimeoutError:
            task.error = f"Task exceeded {self._task_timeout:g}s"
            task.transition(TaskState.TIMED_OUT)
            log_event(
                logger,
                "task.timed_out",
                task=task.plan_ref,
                attempt=task.attempt_count,
                timeout_seconds=self._task_timeout,
            )
            await record_event("task.timed_out", timeout_seconds=self._task_timeout)
            await self._handle_failure(task)
            return
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
            retryable = is_retryable(exc)
            await record_event("task.failed", error=task.error, retryable=retryable)
            await self._handle_failure(task, retryable=retryable)
            return

        if result.response.findings:
            # Each quote looked for in what this task read (ADR-022). Code, not a model.
            result.findings = check_findings(result.response.findings, result.tool_outcomes)
            await record_event(
                "findings.checked",
                total=len(result.findings),
                checked=sum(f.status is FindingStatus.CHECKED for f in result.findings),
                findings=[f.model_dump(mode="json") for f in result.findings[:30]],
            )
        task.result = result
        task.llm_calls.extend(result.llm_calls)
        task.tool_outcomes.extend(result.tool_outcomes)
        task.transition(TaskState.SUCCEEDED)
        log_event(logger, "task.succeeded", task=task.plan_ref, attempt=task.attempt_count)
        await self._save(task)
        await record_event("task.succeeded", answer=result.response.answer[:2000])

    async def _handle_failure(self, task: TaskExecution, *, retryable: bool = True) -> None:
        """Retry with backoff, or give up permanently.

        A retry returns the task to READY — the normal path — rather than a
        special retry state. One code path for "about to run" means retried
        tasks cannot behave differently from first attempts.
        """
        await self._save(task)  # the FAILED / TIMED_OUT state, visible while it waits
        if retryable and should_retry(task.attempt_count, task.max_attempts):
            delay = backoff_delay(task.attempt_count - 1)
            await record_event("task.retrying", delay_seconds=round(delay, 3))
            log_event(
                logger,
                "task.retrying",
                task=task.plan_ref,
                attempt=task.attempt_count,
                delay_seconds=round(delay, 3),
            )
            # The instrument existed from V0.9 and nothing ever incremented it,
            # so the retry rate read as a permanent zero — indistinguishable from
            # "no task has ever been retried".
            instruments().retries.add(1, safe_labels(status=task.state.value))
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
            await self._save(task)
            await record_event(
                "task.permanently_failed", attempts=task.attempt_count, error=task.error
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
            if upstream.result is None:
                # A combining task running on partial inputs is told what is missing
                # and why, so it can say so rather than paper over it (ADR-023).
                context.append(
                    f"- {dep}: {upstream.description}\n  DID NOT FINISH"
                    f" ({upstream.error or upstream.state.value}). Work without it, and say"
                    " plainly what is missing because of it."
                )
                continue
            if upstream.result is not None:
                # The task's id and the sources it read travel with its result
                # (V1.10), so a writer can cite a url — or the task itself, for a
                # computed figure — rather than paraphrasing a task's description.
                sources = sorted(
                    {
                        source
                        for outcome in upstream.result.tool_outcomes
                        if outcome.succeeded
                        for source in sources_in(outcome.output, outcome.name)
                    }
                )
                line = f"- {dep}: {upstream.description}" + _findings_block(upstream.result)
                line += f"\n  Result: {upstream.result.response.answer}"
                if sources:
                    line += "\n  Sources it read: " + ", ".join(sources[:12])
                context.append(line)

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


def _resumed_result(answer: str) -> AgentResult:
    """An AgentResult standing in for work a previous attempt already did.

    It carries **no `llm_calls`**, deliberately. The tokens were spent on the
    earlier attempt and counted against it; counting them again would make a
    resumed run look more expensive than it was and inflate every token total
    derived from it.

    `confidence` is MEDIUM rather than whatever the original attempt reported:
    the stored row keeps the answer, not the confidence, and inventing HIGH here
    would be asserting something that was never recorded.
    """
    return AgentResult(
        request_id="",
        response=AgentResponse(
            answer=answer,
            reasoning="Completed on an earlier attempt at this run; resumed from the stored task.",
            confidence=Confidence.MEDIUM,
        ),
    )


def plan_task_count(plan: Plan) -> int:
    return len(plan.tasks)


__all__ = [
    "DEFAULT_TASK_TIMEOUT_SECONDS",
    "ExecutionReport",
    "Executor",
    "PlannedTask",
    "RunOutcome",
    "TaskExecution",
    "TaskRunner",
]


def is_retryable(exc: AmosError) -> bool:
    """Whether another attempt could possibly succeed (ADR-014).

    A **daily** quota resets at midnight Pacific, and a rejected key is rejected
    every time; retrying either spends calls on a known outcome. A per-minute
    limit, a timeout or an unscoped 429 does pass, so backoff is right for them.
    """
    if isinstance(exc, ProviderAuthError | BudgetExhaustedError):
        return False
    if isinstance(exc, ProviderRateLimitError):
        return exc.details.get("quota_scope") != "day"
    return True


#: Findings handed on per task: enough for a dozen named items, bounded for the writer's prompt.
MAX_HANDED_FINDINGS = 15


def _findings_block(result: AgentResult) -> str:
    """A task's findings for the tasks that depend on it (ADR-022).

    Checked findings are handed on as facts, with the source the quote was found
    on — corrected, if the agent cited the wrong page. The rest are kept, under a
    heading that says they are not confirmed: dropping them would hide work, and
    passing them as facts would launder them.
    """
    checked = result.findings or [
        CheckedFinding(**f.model_dump(), status=FindingStatus.QUOTE_NOT_FOUND)
        for f in result.response.findings
    ]
    if not checked:
        return ""
    confirmed = [f for f in checked if f.status is FindingStatus.CHECKED][:MAX_HANDED_FINDINGS]
    doubtful = [f for f in checked if f.status is not FindingStatus.CHECKED][:MAX_HANDED_FINDINGS]
    block = ""
    if confirmed:
        block += "\n  Findings (each quote was found on its source by code; use these as facts):"
        block += "".join(f"\n    - {f.fact} (source: {f.found_in or f.source})" for f in confirmed)
    if doubtful:
        block += "\n  Not confirmed on any page it read (do not present as fact):"
        block += "".join(f"\n    - {f.fact} (claimed source: {f.source})" for f in doubtful)
    return block
