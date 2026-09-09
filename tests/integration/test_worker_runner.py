"""The worker loop: execution, failure handling and the poison-message ceiling."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.database.engine import session_scope
from amos.database.models import Run
from amos.database.repository import RunRepository
from amos.errors import ProviderTimeoutError
from amos.worker.queue import RunStatus, enqueue
from amos.worker.runner import Worker

pytestmark = pytest.mark.asyncio


def result(answer: str = "done") -> AgentResult:
    return AgentResult(
        request_id="r",
        response=AgentResponse(answer=answer, reasoning="x", confidence=Confidence.HIGH),
        total_tokens=5,
    )


class ScriptedAgent:
    tool_names: list[str] = []

    def __init__(self, outcome: object = None) -> None:
        self.outcome = outcome or result()
        self.goals: list[str] = []

    async def run(self, goal: str) -> AgentResult:
        self.goals.append(goal)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


async def queue_a_run(factory: async_sessionmaker[AsyncSession], goal: str) -> uuid.UUID:
    async with session_scope(factory) as session:
        run = await RunRepository(session).create_run(goal=goal, request_id="r")
        await enqueue(session, run.id)
        return run.id


async def status_of(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> str:
    async with session_scope(factory) as session:
        r = await session.execute(
            text("SELECT status FROM runs WHERE id = :id"), {"id": str(run_id)}
        )
        return str(r.scalar_one())


async def cleanup(factory: async_sessionmaker[AsyncSession], *ids: uuid.UUID) -> None:
    async with session_scope(factory) as session:
        await session.execute(delete(Run).where(Run.id.in_(ids)))


async def test_the_worker_executes_a_queued_run(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await queue_a_run(db_factory, "a queued goal")
    agent = ScriptedAgent()
    worker = Worker(db_factory, lambda: agent)

    assert await worker.run_once() is True
    assert agent.goals == ["a queued goal"]
    assert await status_of(db_factory, run_id) == RunStatus.COMPLETED
    await cleanup(db_factory, run_id)


async def test_run_once_reports_when_there_is_nothing_to_do(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(db_factory) as session:
        await session.execute(text("UPDATE runs SET status='COMPLETED' WHERE status='QUEUED'"))
    worker = Worker(db_factory, ScriptedAgent)
    assert await worker.run_once() is False


async def test_a_failing_run_does_not_kill_the_worker(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A worker that dies on one bad run stops draining the queue entirely."""
    run_id = await queue_a_run(db_factory, "will fail")
    agent = ScriptedAgent(ProviderTimeoutError("timed out"))
    worker = Worker(db_factory, lambda: agent, max_attempts=3)

    assert await worker.run_once() is True, "the worker handled it and kept going"
    await cleanup(db_factory, run_id)


async def test_an_unexpected_exception_is_also_survived(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Not just AmosError — anything. An unhandled exception in one run must not
    take the worker down."""
    run_id = await queue_a_run(db_factory, "will explode")
    worker = Worker(db_factory, lambda: ScriptedAgent(RuntimeError("boom")))

    assert await worker.run_once() is True
    await cleanup(db_factory, run_id)


async def test_a_run_that_exhausts_its_attempts_is_given_up(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The poison-message ceiling, end to end."""
    run_id = await queue_a_run(db_factory, "always fails")
    async with session_scope(db_factory) as session:
        await session.execute(
            text("UPDATE runs SET attempt_count = 2 WHERE id = :id"), {"id": str(run_id)}
        )

    worker = Worker(db_factory, lambda: ScriptedAgent(ProviderTimeoutError("x")), max_attempts=3)
    await worker.run_once()

    assert await status_of(db_factory, run_id) == RunStatus.FAILED
    await cleanup(db_factory, run_id)


async def test_the_worker_id_identifies_who_holds_a_run(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """So a stuck run names its owner rather than being anonymous."""
    run_id = await queue_a_run(db_factory, "goal")
    worker = Worker(db_factory, ScriptedAgent, worker_id="worker-alpha")
    await worker.run_once()

    async with session_scope(db_factory) as session:
        r = await session.execute(
            text("SELECT claimed_by FROM runs WHERE id = :id"), {"id": str(run_id)}
        )
        assert r.scalar_one() == "worker-alpha"
    await cleanup(db_factory, run_id)


async def test_sweep_returns_abandoned_runs_to_the_queue(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await queue_a_run(db_factory, "abandoned")
    async with session_scope(db_factory) as session:
        await session.execute(
            text(
                "UPDATE runs SET status='RUNNING', claimed_at = now() - interval '1 hour' "
                "WHERE id = :id"
            ),
            {"id": str(run_id)},
        )

    worker = Worker(db_factory, ScriptedAgent, visibility_timeout=600)
    assert await worker.sweep() >= 1
    assert await status_of(db_factory, run_id) == RunStatus.QUEUED
    await cleanup(db_factory, run_id)


async def test_completed_runs_are_counted(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    ids = [await queue_a_run(db_factory, f"g{i}") for i in range(2)]
    worker = Worker(db_factory, ScriptedAgent)
    await worker.run_once()
    await worker.run_once()

    assert worker.runs_completed == 2
    await cleanup(db_factory, *ids)
