"""The executor: dependency ordering, retries, skipping, and termination."""

from __future__ import annotations

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.errors import ProviderTimeoutError
from amos.llm.base import LLMCallRecord
from amos.orchestration.executor import ExecutionReport, Executor, RunOutcome
from amos.orchestration.plan import Plan
from amos.orchestration.state import TaskState


def make_plan(*tasks: dict[str, object]) -> Plan:
    return Plan.model_validate({"tasks": list(tasks)})


def task(task_id: str, *deps: str) -> dict[str, object]:
    return {"id": task_id, "description": f"do {task_id}", "depends_on": list(deps)}


def result(answer: str = "done") -> AgentResult:
    return AgentResult(
        request_id="r",
        response=AgentResponse(answer=answer, reasoning="because", confidence=Confidence.HIGH),
        llm_calls=[LLMCallRecord(provider="fake", model="m", prompt_tokens=1, output_tokens=1)],
        total_tokens=2,
    )


class ScriptedRunner:
    """Answers per task, so tests fix the agent's behaviour rather than the model's."""

    def __init__(self, behaviour: dict[str, object] | None = None) -> None:
        self.behaviour = behaviour or {}
        self.goals: list[str] = []

    async def run(self, goal: str) -> AgentResult:
        self.goals.append(goal)
        for key, outcome in self.behaviour.items():
            if key in goal:
                if isinstance(outcome, Exception):
                    raise outcome
                if callable(outcome):
                    return outcome(len([g for g in self.goals if key in g]))
                return outcome  # type: ignore[return-value]
        return result()


async def no_sleep(_seconds: float) -> None:
    """Retry tests must not actually wait for exponential backoff."""


def by_ref(report: ExecutionReport) -> dict[str, TaskState]:
    return {t.plan_ref: t.state for t in report.tasks}


# ---------- ordering ----------


async def test_single_task_runs_and_succeeds() -> None:
    report = await Executor(ScriptedRunner(), sleep=no_sleep).execute(make_plan(task("t1")))
    assert report.outcome == RunOutcome.COMPLETED
    assert by_ref(report) == {"t1": TaskState.SUCCEEDED}


async def test_dependencies_run_before_dependents() -> None:
    runner = ScriptedRunner()
    plan = make_plan(task("t1"), task("t2", "t1"), task("t3", "t2"))
    await Executor(runner, sleep=no_sleep).execute(plan)

    order = [g.split("\n")[0] for g in runner.goals]
    assert order == ["do t1", "do t2", "do t3"]


async def test_independent_tasks_run_together() -> None:
    """The DAG's payoff over a list: no false serialisation."""
    runner = ScriptedRunner()
    report = await Executor(runner, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2"), task("t3"))
    )
    assert report.outcome == RunOutcome.COMPLETED
    assert len(runner.goals) == 3


async def test_dependency_results_are_passed_to_dependents() -> None:
    """Each description is self-contained, so upstream results must be supplied."""
    runner = ScriptedRunner({"do t1": result("the answer is 42")})
    await Executor(runner, sleep=no_sleep).execute(make_plan(task("t1"), task("t2", "t1")))

    downstream_goal = next(g for g in runner.goals if g.startswith("do t2"))
    assert "the answer is 42" in downstream_goal


async def test_diamond_dependencies_execute_correctly() -> None:
    runner = ScriptedRunner()
    plan = make_plan(task("t1"), task("t2", "t1"), task("t3", "t1"), task("t4", "t2", "t3"))
    report = await Executor(runner, sleep=no_sleep).execute(plan)

    assert report.outcome == RunOutcome.COMPLETED
    order = [g.split("\n")[0] for g in runner.goals]
    assert order[0] == "do t1"
    assert order[-1] == "do t4"


# ---------- retries ----------


async def test_failing_task_is_retried_then_permanently_fails() -> None:
    runner = ScriptedRunner({"do t1": ProviderTimeoutError("timed out")})
    report = await Executor(runner, max_attempts=3, sleep=no_sleep).execute(make_plan(task("t1")))

    assert by_ref(report) == {"t1": TaskState.PERMANENTLY_FAILED}
    assert report.outcome == RunOutcome.FAILED
    assert len(runner.goals) == 3, "one initial attempt plus two retries"


async def test_a_task_that_recovers_on_retry_succeeds() -> None:
    def flaky(attempt: int) -> AgentResult:
        if attempt == 1:
            raise ProviderTimeoutError("transient")
        return result("recovered")

    runner = ScriptedRunner({"do t1": flaky})
    report = await Executor(runner, max_attempts=3, sleep=no_sleep).execute(make_plan(task("t1")))

    assert by_ref(report) == {"t1": TaskState.SUCCEEDED}
    assert report.tasks[0].attempt_count == 2


async def test_retry_budget_is_per_task_not_per_run() -> None:
    runner = ScriptedRunner({"do t": ProviderTimeoutError("boom")})
    report = await Executor(runner, max_attempts=2, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2"))
    )
    assert len(runner.goals) == 4, "2 tasks x 2 attempts"
    assert all(s is TaskState.PERMANENTLY_FAILED for s in by_ref(report).values())


async def test_max_attempts_of_one_means_no_retry() -> None:
    runner = ScriptedRunner({"do t1": ProviderTimeoutError("boom")})
    await Executor(runner, max_attempts=1, sleep=no_sleep).execute(make_plan(task("t1")))
    assert len(runner.goals) == 1


# ---------- skipping ----------


async def test_dependents_of_a_failed_task_are_skipped() -> None:
    runner = ScriptedRunner({"do t1": ProviderTimeoutError("boom")})
    report = await Executor(runner, max_attempts=1, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2", "t1"))
    )

    states = by_ref(report)
    assert states["t1"] is TaskState.PERMANENTLY_FAILED
    assert states["t2"] is TaskState.SKIPPED
    assert "t1" in (report.tasks[1].error or "")


async def test_skipping_propagates_transitively() -> None:
    """t1 fails → t2 skipped → t3 must also be skipped, not left waiting."""
    runner = ScriptedRunner({"do t1": ProviderTimeoutError("boom")})
    report = await Executor(runner, max_attempts=1, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2", "t1"), task("t3", "t2"))
    )

    states = by_ref(report)
    assert states["t2"] is TaskState.SKIPPED
    assert states["t3"] is TaskState.SKIPPED
    assert len(runner.goals) == 1, "only the failing task ever ran"


async def test_an_unrelated_branch_still_runs_when_another_fails() -> None:
    """Failure is contained to its own subtree."""
    runner = ScriptedRunner({"do t1": ProviderTimeoutError("boom")})
    report = await Executor(runner, max_attempts=1, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2", "t1"), task("t3"))
    )

    states = by_ref(report)
    assert states["t1"] is TaskState.PERMANENTLY_FAILED
    assert states["t2"] is TaskState.SKIPPED
    assert states["t3"] is TaskState.SUCCEEDED


# ---------- outcomes ----------


async def test_partial_completion_is_its_own_outcome() -> None:
    """Some work succeeding and some failing is a real result, not an error."""
    runner = ScriptedRunner({"do t2": ProviderTimeoutError("boom")})
    report = await Executor(runner, max_attempts=1, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2"))
    )

    assert report.outcome == RunOutcome.PARTIALLY_COMPLETED
    assert len(report.succeeded) == 1


async def test_everything_failing_is_a_failed_run() -> None:
    runner = ScriptedRunner({"do t": ProviderTimeoutError("boom")})
    report = await Executor(runner, max_attempts=1, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2"))
    )
    assert report.outcome == RunOutcome.FAILED


async def test_every_task_reaches_a_terminal_state() -> None:
    """The executor's termination guarantee, on a graph mixing all outcomes."""
    runner = ScriptedRunner({"do t2": ProviderTimeoutError("boom")})
    report = await Executor(runner, max_attempts=2, sleep=no_sleep).execute(
        make_plan(task("t1"), task("t2"), task("t3", "t2"), task("t4", "t1"))
    )

    from amos.orchestration.state import is_terminal

    assert all(is_terminal(t.state) for t in report.tasks)
    assert report.outcome == RunOutcome.PARTIALLY_COMPLETED


async def test_tokens_are_aggregated_across_every_task_and_attempt() -> None:
    runner = ScriptedRunner()
    report = await Executor(runner, sleep=no_sleep).execute(make_plan(task("t1"), task("t2")))
    assert report.total_tokens == 4
    assert len(report.all_llm_calls) == 2
