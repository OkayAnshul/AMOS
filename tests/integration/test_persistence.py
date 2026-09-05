"""Persistence and trace assembly against a real PostgreSQL.

Skipped automatically when no database is running.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.api.persistence import RunService
from amos.database.models import Run
from amos.database.repository import RunRepository, RunStatus
from amos.errors import ProviderTimeoutError
from amos.llm.base import LLMCallRecord
from amos.tools.base import ToolOutcome, ToolStatus

pytestmark = pytest.mark.asyncio


def sample_result(request_id: str = "req-1") -> AgentResult:
    """An AgentResult exactly as V0.1/V0.2 produce one."""
    return AgentResult(
        request_id=request_id,
        response=AgentResponse(
            answer="485.8",
            reasoning="Used the calculator.",
            assumptions=["Percent means /100."],
            confidence=Confidence.HIGH,
            caveats=[],
        ),
        llm_calls=[
            LLMCallRecord(
                provider="fake", model="m", prompt_tokens=10, output_tokens=5, latency_ms=12
            ),
            LLMCallRecord(
                provider="fake", model="m", prompt_tokens=20, output_tokens=8, latency_ms=30
            ),
        ],
        tool_outcomes=[
            ToolOutcome(
                call_id="c1",
                name="calculator",
                status=ToolStatus.OK,
                output={"result": 485.8},
                latency_ms=1,
            )
        ],
        total_tokens=43,
        latency_ms=120,
    )


class FakeAgent:
    tool_names = ["calculator"]

    def __init__(self, result: AgentResult | Exception) -> None:
        self._result = result
        self.calls = 0

    async def run(self, goal: str) -> AgentResult:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


# ---------- repository ----------


async def test_run_is_persisted_with_full_trace(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    run = await repo.create_run(goal="What is 17% of 2340 plus 88?", request_id="req-1")
    await repo.record_success(run, sample_result())

    stored = await repo.get_trace(run.id)
    assert stored is not None
    assert stored.status == RunStatus.COMPLETED
    assert stored.total_tokens == 43
    assert stored.result is not None
    assert stored.result["answer"] == "485.8"
    assert len(stored.steps) == 1
    assert len(stored.llm_calls) == 2
    assert len(stored.tool_calls) == 1
    assert stored.tool_calls[0].tool_name == "calculator"
    assert stored.completed_at is not None


async def test_trace_is_complete_every_call_reachable_from_the_run(
    db_session: AsyncSession,
) -> None:
    """The V0.3 promise: nothing that happened is missing from the trace."""
    repo = RunRepository(db_session)
    result = sample_result()
    run = await repo.create_run(goal="g", request_id="r")
    await repo.record_success(run, result)

    stored = await repo.get_trace(run.id)
    assert stored is not None
    assert len(stored.llm_calls) == len(result.llm_calls)
    assert len(stored.tool_calls) == len(result.tool_outcomes)
    # every child row points back at the run — the denormalisation that makes
    # trace assembly one filter instead of a four-table join
    assert all(c.run_id == run.id for c in stored.llm_calls)
    assert all(t.run_id == run.id for t in stored.tool_calls)


async def test_failed_run_keeps_its_partial_trace(db_session: AsyncSession) -> None:
    """A failure that burned tokens must show that it did."""
    repo = RunRepository(db_session)
    run = await repo.create_run(goal="g", request_id="r")
    await repo.record_failure(run, "ProviderTimeoutError", "timed out", sample_result())

    stored = await repo.get_trace(run.id)
    assert stored is not None
    assert stored.status == RunStatus.FAILED
    assert stored.error is not None
    assert stored.error["type"] == "ProviderTimeoutError"
    assert stored.total_tokens == 43, "the tokens were still spent"
    assert len(stored.llm_calls) == 2
    assert stored.steps[0].status == "FAILED"


async def test_run_is_recorded_before_execution(db_session: AsyncSession) -> None:
    """A crash mid-run must still leave evidence the run was attempted."""
    repo = RunRepository(db_session)
    run = await repo.create_run(goal="g", request_id="r")
    assert run.status == RunStatus.RECEIVED

    stored = await repo.get_trace(run.id)
    assert stored is not None, "the row exists before any result does"


async def test_unknown_run_id_returns_none(db_session: AsyncSession) -> None:
    assert await RunRepository(db_session).get_trace(uuid.uuid4()) is None


async def test_cascade_delete_removes_child_rows(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    run = await repo.create_run(goal="g", request_id="r")
    await repo.record_success(run, sample_result())
    run_id = run.id

    await db_session.execute(delete(Run).where(Run.id == run_id))
    await db_session.flush()
    assert await repo.get_trace(run_id) is None


# ---------- idempotency ----------


async def test_idempotent_resubmit_returns_the_original_run(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Without this, a client timeout plus a retry silently doubles the cost."""
    agent = FakeAgent(sample_result())
    service = RunService(agent, db_factory)
    key = f"key-{uuid.uuid4()}"

    _, first_id = await service.execute("goal", request_id="r1", idempotency_key=key)
    _, second_id = await service.execute("goal", request_id="r2", idempotency_key=key)

    assert first_id == second_id
    assert agent.calls == 1, "the second submit must not re-run the agent"

    async with db_factory() as session:
        await session.execute(delete(Run).where(Run.id == first_id))
        await session.commit()


async def test_different_keys_create_different_runs(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    agent = FakeAgent(sample_result())
    service = RunService(agent, db_factory)

    _, a = await service.execute("goal", request_id="r", idempotency_key=f"k-{uuid.uuid4()}")
    _, b = await service.execute("goal", request_id="r", idempotency_key=f"k-{uuid.uuid4()}")

    assert a != b
    assert agent.calls == 2

    async with db_factory() as session:
        await session.execute(delete(Run).where(Run.id.in_([a, b])))
        await session.commit()


async def test_no_key_means_no_deduplication(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    agent = FakeAgent(sample_result())
    service = RunService(agent, db_factory)

    _, a = await service.execute("goal", request_id="r")
    _, b = await service.execute("goal", request_id="r")

    assert a != b
    assert agent.calls == 2

    async with db_factory() as session:
        await session.execute(delete(Run).where(Run.id.in_([a, b])))
        await session.commit()


async def test_agent_failure_is_recorded_then_reraised(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = RunService(FakeAgent(ProviderTimeoutError("timed out")), db_factory)

    with pytest.raises(ProviderTimeoutError):
        await service.execute("goal", request_id="r")

    async with db_factory() as session:
        runs = await RunRepository(session).list_recent(limit=1)
        assert runs[0].status == RunStatus.FAILED
        assert runs[0].error is not None
        await session.execute(delete(Run).where(Run.id == runs[0].id))
        await session.commit()


# ---------- trace assembly ----------


async def test_trace_is_assembled_from_stored_rows_only(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The trace must be answerable by a process that never saw the run."""
    service = RunService(FakeAgent(sample_result()), db_factory)
    _, run_id = await service.execute("What is 17% of 2340?", request_id="req-x")
    assert run_id is not None

    # A brand-new service instance — nothing in memory from the execution.
    fresh = RunService(FakeAgent(sample_result()), db_factory)
    trace = await fresh.get_trace(run_id)

    assert trace is not None
    assert trace.goal == "What is 17% of 2340?"
    assert trace.status == "COMPLETED"
    assert trace.total_tokens == 43
    assert len(trace.llm_calls) == 2
    assert len(trace.tool_calls) == 1
    assert trace.tool_calls[0].tool_name == "calculator"
    assert trace.result is not None

    async with db_factory() as session:
        await session.execute(delete(Run).where(Run.id == run_id))
        await session.commit()


async def test_trace_records_the_arguments_a_tool_was_called_with(
    db_session: AsyncSession,
) -> None:
    """A trace without inputs is half a trace: you can see what came back but
    not what was asked, which is exactly what you need when debugging."""
    repo = RunRepository(db_session)
    result = sample_result()
    result.tool_outcomes[0].arguments = {"expression": "2340 * 0.17"}

    run = await repo.create_run(goal="g", request_id="r")
    await repo.record_success(run, result)

    stored = await repo.get_trace(run.id)
    assert stored is not None
    assert stored.tool_calls[0].arguments == {"expression": "2340 * 0.17"}


# ---------- V0.4: task persistence ----------


async def test_task_dag_is_persisted_with_resolved_dependencies(
    db_session: AsyncSession,
) -> None:
    """Stored dependencies are row UUIDs, not the planner's symbolic refs, so the
    graph stays intact without the plan text."""
    from amos.agents.schemas import TaskRecord

    repo = RunRepository(db_session)
    result = sample_result()
    result.tasks = [
        TaskRecord(
            plan_ref="t1",
            description="do t1",
            state="SUCCEEDED",
            position=0,
            attempt_count=1,
            answer="a",
        ),
        TaskRecord(
            plan_ref="t2",
            description="do t2",
            state="SUCCEEDED",
            position=1,
            attempt_count=1,
            depends_on=["t1"],
            answer="b",
        ),
    ]
    run = await repo.create_run(goal="g", request_id="r")
    await repo.record_success(run, result)

    stored = await repo.get_trace(run.id)
    assert stored is not None
    assert len(stored.tasks) == 2
    t1, t2 = sorted(stored.tasks, key=lambda t: t.position)
    assert t2.depends_on == [t1.id], "symbolic ref resolved to the real row id"


async def test_partially_completed_run_is_not_recorded_as_success(
    db_session: AsyncSession,
) -> None:
    """A run where some tasks failed must not read as an unqualified success."""
    from amos.agents.schemas import TaskRecord

    repo = RunRepository(db_session)
    result = sample_result()
    result.outcome = "PARTIALLY_COMPLETED"
    result.tasks = [
        TaskRecord(plan_ref="t1", description="ok", state="SUCCEEDED", position=0),
        TaskRecord(
            plan_ref="t2",
            description="bad",
            state="PERMANENTLY_FAILED",
            position=1,
            error="timed out",
        ),
    ]
    run = await repo.create_run(goal="g", request_id="r")
    await repo.record_success(run, result)

    stored = await repo.get_trace(run.id)
    assert stored is not None
    assert stored.status == "PARTIALLY_COMPLETED"
    failed = next(t for t in stored.tasks if t.plan_ref == "t2")
    assert failed.error is not None
    assert failed.error["message"] == "timed out"
