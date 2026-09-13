"""Durable progress: the plan survives, and a second attempt resumes (ADR-010).

Before V1.1 nothing was written until a run finished, so a reclaimed run had no
record of which tasks had already succeeded and could only be re-executed from
the start. These tests are against a real database because the whole claim is
about what survives the process.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.database.engine import session_scope
from amos.database.models import Run, Task
from amos.database.progress import RunProgress
from amos.errors import ProviderTimeoutError
from amos.llm.fake import FakeProvider
from amos.observability import set_current_run_id
from amos.orchestration.executor import Executor, TaskExecution
from amos.orchestration.orchestrator import Orchestrator
from amos.orchestration.plan import Plan
from amos.orchestration.state import TaskState
from tests.conftest import valid_response_json

pytestmark = pytest.mark.asyncio


def make_plan() -> Plan:
    return Plan.model_validate(
        {
            "tasks": [
                {"id": "t1", "description": "gather the sources", "depends_on": []},
                {"id": "t2", "description": "summarise them", "depends_on": ["t1"]},
            ]
        }
    )


def execution(plan_ref: str, state: TaskState, answer: str | None = None) -> TaskExecution:
    task = TaskExecution(plan_ref=plan_ref, description="d", depends_on=[], position=0)
    task.state = state
    task.attempt_count = 1
    if answer is not None:
        task.result = AgentResult(
            request_id="r",
            response=AgentResponse(answer=answer, reasoning="x", confidence=Confidence.HIGH),
        )
    return task


@pytest.fixture
async def run_id(db_factory: async_sessionmaker, factory_actor):  # type: ignore[type-arg,no-untyped-def]
    """A committed run row, removed afterwards.

    Committed rather than rolled back: the point of these tests is what another
    process would see, so the writes have to be real.
    """
    new_id = uuid.uuid4()
    async with session_scope(db_factory) as session:
        session.add(
            Run(
                id=new_id,
                user_id=factory_actor.id,
                goal_text="compare two designs",
                status="QUEUED",
            )
        )
    set_current_run_id(str(new_id))
    try:
        yield new_id
    finally:
        set_current_run_id(None)
        async with session_scope(db_factory) as session:
            await session.execute(delete(Task).where(Task.run_id == new_id))
            await session.execute(delete(Run).where(Run.id == new_id))


async def test_load_returns_nothing_before_a_plan_is_saved(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    """The normal first attempt: no rows, so the caller plans as usual."""
    assert await RunProgress(db_factory).load() is None


async def test_a_saved_plan_comes_back_without_the_planner(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    progress = RunProgress(db_factory)
    await progress.save(make_plan())

    loaded = await progress.load()
    assert loaded is not None
    plan, completed = loaded

    assert [t.id for t in plan.tasks] == ["t1", "t2"]
    # depends_on is stored as row UUIDs and mapped back to the planner's
    # symbolic refs, so the restored plan is the same graph.
    assert plan.tasks[1].depends_on == ["t1"]
    assert completed == {}


async def test_a_checkpointed_task_comes_back_as_completed_work(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    progress = RunProgress(db_factory)
    await progress.save(make_plan())

    await progress.record(execution("t1", TaskState.SUCCEEDED, answer="four sources found"))

    loaded = await progress.load()
    assert loaded is not None
    _, completed = loaded
    assert completed == {"t1": "four sources found"}


async def test_a_failed_task_is_recorded_but_is_not_completed_work(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    """A resumed run must redo what failed, and skip only what succeeded."""
    progress = RunProgress(db_factory)
    await progress.save(make_plan())

    failed = execution("t1", TaskState.PERMANENTLY_FAILED)
    failed.error = "provider timed out"
    await progress.record(failed)

    loaded = await progress.load()
    assert loaded is not None
    _, completed = loaded
    assert completed == {}

    async with session_scope(db_factory) as session:
        row = (
            await session.execute(select(Task).where(Task.run_id == run_id, Task.plan_ref == "t1"))
        ).scalar_one()
        assert row.state == TaskState.PERMANENTLY_FAILED.value
        assert row.error == {"message": "provider timed out"}


async def test_the_plan_is_persisted_before_anything_runs(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    """A crash between planning and the first task must still leave a record of
    what was going to be attempted.
    """
    await RunProgress(db_factory).save(make_plan())

    async with session_scope(db_factory) as session:
        rows = (
            (
                await session.execute(
                    select(Task).where(Task.run_id == run_id).order_by(Task.position)
                )
            )
            .scalars()
            .all()
        )
    assert [r.plan_ref for r in rows] == ["t1", "t2"]
    assert {r.state for r in rows} == {TaskState.PENDING.value}


async def test_progress_is_inert_without_a_run_in_context(
    db_factory: async_sessionmaker,
) -> None:
    """Nothing is written when there is no run — the V0.2 path, and any caller
    that never set one.
    """
    set_current_run_id(None)
    progress = RunProgress(db_factory)

    assert await progress.load() is None
    await progress.save(make_plan())
    await progress.record(execution("t1", TaskState.SUCCEEDED, answer="x"))


async def test_a_checkpoint_for_an_unplanned_task_is_not_an_error(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    """Persistence may be enabled after a run was planned without it."""
    await RunProgress(db_factory).record(execution("t1", TaskState.SUCCEEDED, answer="x"))


class CountingRunner:
    """Records every goal it is asked to run, and can fail on demand."""

    tool_names: list[str] = []

    def __init__(self, fail_on: str | None = None) -> None:
        self.goals: list[str] = []
        self._fail_on = fail_on

    async def run(self, goal: str) -> AgentResult:
        self.goals.append(goal)
        if self._fail_on and self._fail_on in goal:
            raise ProviderTimeoutError("provider timed out")
        return AgentResult(
            request_id="r",
            response=AgentResponse(
                answer=f"did: {goal}", reasoning="x", confidence=Confidence.HIGH
            ),
        )


def orchestrator_for(
    runner: CountingRunner, progress: RunProgress, planner: object
) -> Orchestrator:
    """An orchestrator wired exactly as `build_agent` wires it with a database."""
    return Orchestrator(
        # One synthesis call per run; a handful is plenty and keeps the test from
        # asserting on an exact call count it does not care about.
        FakeProvider([valid_response_json() for _ in range(4)]),
        runner,  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
        plan_store=progress,
        executor=Executor(runner, max_attempts=1, checkpoint=progress, sleep=_no_sleep),  # type: ignore[arg-type]
    )


async def _no_sleep(_seconds: float) -> None: ...


class StubPlanner:
    """Returns a fixed plan, and counts how often it was asked.

    The count is the assertion that matters on resume: calling the planner again
    would produce a *different* DAG, so the stored refs would no longer refer to
    anything.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, goal: str, calls: list[object]) -> Plan:
        self.calls += 1
        return make_plan()


async def test_a_second_attempt_resumes_instead_of_re_executing(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    """The V1.1 headline.

    First attempt: t1 succeeds, t2 fails, the run is partially complete. Second
    attempt — the same run, as a reclaim would be — must not re-run t1, must not
    re-plan, and must finish t2.
    """
    progress = RunProgress(db_factory)
    planner = StubPlanner()

    first = CountingRunner(fail_on="summarise them")
    report_one = await orchestrator_for(first, progress, planner).run("compare two designs")

    assert report_one.outcome == "PARTIALLY_COMPLETED"
    # startswith, not `in`: a dependent's goal *contains* its upstream's
    # description as context, so `in` would match t1 while only t2 ran.
    assert any(g.startswith("gather the sources") for g in first.goals)
    assert any(g.startswith("summarise them") for g in first.goals)
    assert planner.calls == 1

    # --- the reclaim ---
    second = CountingRunner()
    report_two = await orchestrator_for(second, progress, planner).run("compare two designs")

    assert report_two.outcome == "COMPLETED"
    # t1 was done last time and is not touched again...
    assert not any(g.startswith("gather the sources") for g in second.goals)
    # ...t2 is retried, and receives t1's stored answer as context.
    retried = next(g for g in second.goals if g.startswith("summarise them"))
    assert "did: gather the sources" in retried
    # ...and the planner was never asked a second time.
    assert planner.calls == 1


async def test_resuming_a_fully_completed_run_runs_nothing(
    db_factory: async_sessionmaker,
    run_id: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    """At-least-once means a run can be claimed again after finishing. That must
    cost nothing rather than redoing everything.
    """
    progress = RunProgress(db_factory)
    planner = StubPlanner()

    first = CountingRunner()
    await orchestrator_for(first, progress, planner).run("compare two designs")
    assert len(first.goals) == 2

    second = CountingRunner()
    report = await orchestrator_for(second, progress, planner).run("compare two designs")

    assert second.goals == []
    assert report.outcome == "COMPLETED"
