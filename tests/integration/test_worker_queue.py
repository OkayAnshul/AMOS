"""SKIP LOCKED claiming, visibility timeouts and crash recovery.

These are the tests that matter for V0.8. They run against a real PostgreSQL
because **the guarantees are the SQL** — concurrency, row locking and atomic
claim-plus-state-change cannot be verified against a fake without testing the
fake.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from amos.auth import Actor
from amos.database.models import Run
from amos.database.repository import RunRepository
from amos.worker.queue import (
    RunStatus,
    claim_next_run,
    enqueue,
    give_up,
    list_dead_letter,
    queue_depth,
    reclaim_abandoned_runs,
)

pytestmark = pytest.mark.asyncio


async def queue_a_run(session: AsyncSession, actor: Actor, goal: str = "a goal") -> uuid.UUID:
    run = await RunRepository(session, actor).create_run(goal=goal, request_id="r")
    await enqueue(session, run.id)
    return run.id


async def status_of(session: AsyncSession, run_id: uuid.UUID) -> str:
    result = await session.execute(
        text("SELECT status FROM runs WHERE id = :id"), {"id": str(run_id)}
    )
    return str(result.scalar_one())


# ---------- claiming ----------


async def test_a_queued_run_can_be_claimed(db_session: AsyncSession, actor: Actor) -> None:
    run_id = await queue_a_run(db_session, actor)
    claimed = await claim_next_run(db_session, "worker-1")

    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.attempt_count == 1
    assert await status_of(db_session, run_id) == RunStatus.RUNNING


async def test_claiming_is_atomic_with_the_status_change(
    db_session: AsyncSession,
    actor: Actor,
) -> None:
    """The property that makes a dead worker recoverable without recovery code:
    the row is locked and marked RUNNING in one statement, one transaction."""
    run_id = await queue_a_run(db_session, actor)
    await claim_next_run(db_session, "worker-1")

    result = await db_session.execute(
        text("SELECT claimed_by, claimed_at FROM runs WHERE id = :id"),
        {"id": str(run_id)},
    )
    row = result.first()
    assert row is not None
    assert row.claimed_by == "worker-1"
    assert row.claimed_at is not None


async def test_an_empty_queue_returns_none(db_session: AsyncSession) -> None:
    await db_session.execute(text("UPDATE runs SET status = 'COMPLETED' WHERE status = 'QUEUED'"))
    assert await claim_next_run(db_session, "worker-1") is None


async def test_a_claimed_run_is_not_claimed_again(db_session: AsyncSession, actor: Actor) -> None:
    await db_session.execute(text("UPDATE runs SET status = 'COMPLETED' WHERE status = 'QUEUED'"))
    await queue_a_run(db_session, actor)

    first = await claim_next_run(db_session, "worker-1")
    second = await claim_next_run(db_session, "worker-2")

    assert first is not None
    assert second is None, "the only queued run was already taken"


# ---------- concurrency: the actual point of SKIP LOCKED ----------


async def test_concurrent_workers_claim_disjoint_runs(
    db_factory: async_sessionmaker[AsyncSession],
    factory_actor: Actor,
) -> None:
    """The core guarantee. Without SKIP LOCKED, worker 2 would BLOCK on the row
    worker 1 has locked and the workers would serialise into one.
    """
    from amos.database.engine import session_scope

    async with session_scope(db_factory) as session:
        ids = [await queue_a_run(session, factory_actor, f"goal {i}") for i in range(6)]

    async def claim(worker: str) -> list[uuid.UUID]:
        taken: list[uuid.UUID] = []
        for _ in range(3):
            async with session_scope(db_factory) as session:
                claimed = await claim_next_run(session, worker)
            if claimed is not None:
                taken.append(claimed.run_id)
        return taken

    a, b, c = await asyncio.gather(claim("w1"), claim("w2"), claim("w3"))
    all_claimed = a + b + c

    assert len(all_claimed) == len(set(all_claimed)), "a run was claimed twice"
    assert set(all_claimed) >= set(ids), "some queued run was never claimed"

    async with session_scope(db_factory) as session:
        await session.execute(delete(Run).where(Run.id.in_(ids)))


async def test_every_queued_run_is_eventually_claimed(
    db_factory: async_sessionmaker[AsyncSession],
    factory_actor: Actor,
) -> None:
    """SKIP LOCKED skips *locked* rows, not queued ones — nothing is starved."""
    from amos.database.engine import session_scope

    async with session_scope(db_factory) as session:
        ids = [await queue_a_run(session, factory_actor, f"g{i}") for i in range(5)]

    claimed: set[uuid.UUID] = set()
    for _ in range(10):
        async with session_scope(db_factory) as session:
            got = await claim_next_run(session, "solo")
        if got is None:
            break
        claimed.add(got.run_id)

    assert set(ids) <= claimed

    async with session_scope(db_factory) as session:
        await session.execute(delete(Run).where(Run.id.in_(ids)))


# ---------- crash recovery ----------


async def test_an_abandoned_run_is_reclaimed(db_session: AsyncSession, actor: Actor) -> None:
    """What makes a worker crash survivable: nothing has to notice the worker
    died, only that the run has been RUNNING too long."""
    run_id = await queue_a_run(db_session, actor)
    await claim_next_run(db_session, "doomed-worker")

    # Simulate the worker having died 20 minutes ago.
    await db_session.execute(
        text("UPDATE runs SET claimed_at = now() - interval '20 minutes' WHERE id = :id"),
        {"id": str(run_id)},
    )
    reclaimed = await reclaim_abandoned_runs(db_session, visibility_timeout=600)

    assert reclaimed >= 1
    assert await status_of(db_session, run_id) == RunStatus.QUEUED


async def test_a_healthy_run_is_not_stolen(db_session: AsyncSession, actor: Actor) -> None:
    """A slow-but-alive worker must keep its run, or the timeout causes the
    duplicate execution it exists to recover from."""
    run_id = await queue_a_run(db_session, actor)
    await claim_next_run(db_session, "busy-worker")

    await reclaim_abandoned_runs(db_session, visibility_timeout=600)
    assert await status_of(db_session, run_id) == RunStatus.RUNNING


async def test_a_reclaimed_run_can_be_claimed_again(db_session: AsyncSession, actor: Actor) -> None:
    run_id = await queue_a_run(db_session, actor)
    await claim_next_run(db_session, "worker-1")
    await db_session.execute(
        text("UPDATE runs SET claimed_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": str(run_id)},
    )
    await reclaim_abandoned_runs(db_session, visibility_timeout=600)

    again = await claim_next_run(db_session, "worker-2")
    assert again is not None
    assert again.run_id == run_id
    assert again.attempt_count == 2, "the retry is visible in the data"


# ---------- poison messages ----------


async def test_a_run_that_exhausts_its_attempts_stops_being_claimed(
    db_session: AsyncSession,
    actor: Actor,
) -> None:
    """Without an attempt ceiling, a run that kills every worker is reclaimed
    forever and quietly consumes the queue."""
    await db_session.execute(text("UPDATE runs SET status = 'COMPLETED' WHERE status = 'QUEUED'"))
    run_id = await queue_a_run(db_session, actor)
    await db_session.execute(
        text("UPDATE runs SET attempt_count = 3 WHERE id = :id"), {"id": str(run_id)}
    )

    assert await claim_next_run(db_session, "w", max_attempts=3) is None


async def test_giving_up_records_why(db_session: AsyncSession, actor: Actor) -> None:
    run_id = await queue_a_run(db_session, actor)
    await give_up(db_session, run_id, "crashed three times")

    result = await db_session.execute(
        text("SELECT status, error FROM runs WHERE id = :id"), {"id": str(run_id)}
    )
    row = result.first()
    assert row is not None
    # DEAD_LETTER since V1.1, not FAILED. Deliberate and observable: a FAILED run
    # executed and produced a verdict, and this one never did.
    assert row.status == RunStatus.DEAD_LETTER
    assert "crashed three times" in str(row.error)


async def test_queue_depth_counts_only_queued_runs(db_session: AsyncSession, actor: Actor) -> None:
    before = await queue_depth(db_session)
    await queue_a_run(db_session, actor)
    assert await queue_depth(db_session) == before + 1


async def test_giving_up_dead_letters_rather_than_failing(
    db_session: AsyncSession, actor: Actor
) -> None:
    """A FAILED run executed and produced a verdict. A dead-lettered one never
    did — the worker died on it repeatedly and the queue stopped retrying.

    Before V1.1 both landed in FAILED, so the queue's give-ups were
    indistinguishable from ordinary failures and nothing collected them.
    """
    run_id = await queue_a_run(db_session, actor, "a goal that kills workers")

    await give_up(db_session, run_id, "attempts exhausted")

    status = (
        await db_session.execute(
            text("SELECT status FROM runs WHERE id = :id"), {"id": str(run_id)}
        )
    ).scalar_one()
    assert status == RunStatus.DEAD_LETTER


async def test_a_dead_lettered_run_is_not_claimable(db_session: AsyncSession, actor: Actor) -> None:
    """The whole reason the ceiling exists: one bad run must not be reclaimed
    forever and starve every good one.
    """
    run_id = await queue_a_run(db_session, actor, "poison")
    await give_up(db_session, run_id, "attempts exhausted")

    assert await claim_next_run(db_session, "w1") is None


async def test_dead_letter_listing_carries_what_a_reviewer_needs(
    db_session: AsyncSession,
    actor: Actor,
) -> None:
    run_id = await queue_a_run(db_session, actor, "compare two designs")
    await db_session.execute(
        text("UPDATE runs SET attempt_count = 3, claimed_by = 'host:99' WHERE id = :id"),
        {"id": str(run_id)},
    )
    await give_up(db_session, run_id, "attempts exhausted after 3 tries")

    listed = await list_dead_letter(db_session)

    entry = next(e for e in listed if e.run_id == str(run_id))
    assert entry.goal == "compare two designs"
    assert entry.attempt_count == 3
    assert entry.last_worker == "host:99"
    assert entry.reason == "attempts exhausted after 3 tries"


async def test_a_reason_containing_quotes_does_not_corrupt_the_error_json(
    db_session: AsyncSession,
    actor: Actor,
) -> None:
    """`reason` carries an exception message. It was interpolated into a JSON
    string with an f-string, so a quote or backslash in it produced malformed
    JSON that Postgres rejected — turning a give-up into a crash, in the code
    path that exists to handle crashes.
    """
    run_id = await queue_a_run(db_session, actor, "quoting")
    nasty = 'provider said "no" \\ then gave up'

    await give_up(db_session, run_id, nasty)

    listed = await list_dead_letter(db_session)
    assert next(e for e in listed if e.run_id == str(run_id)).reason == nasty


async def test_a_stored_trace_context_survives_enqueue_and_claim(
    db_session: AsyncSession,
    actor: Actor,
) -> None:
    """The column round-trips, so the worker can continue the submitting
    request's trace instead of starting a second one.

    Deliberately does **not** install a tracer provider. `set_tracer_provider`
    works once per process and later calls are ignored with a warning, so a test
    that grabs it here silently blinds every span-capturing test that runs
    afterwards. Span parentage is covered in `tests/unit/telemetry/`, where the
    provider is owned for the whole module; what is being tested here is
    storage and retrieval, which needs no tracing at all.
    """
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    run_id = await queue_a_run(db_session, actor, "traced")
    await db_session.execute(
        text("UPDATE runs SET trace_parent = :tp WHERE id = :id"),
        {"tp": traceparent, "id": str(run_id)},
    )

    claimed = await claim_next_run(db_session, "worker-1")

    assert claimed is not None
    assert claimed.trace_parent == traceparent


async def test_enqueue_records_whatever_trace_context_is_current(
    db_session: AsyncSession,
    actor: Actor,
) -> None:
    """With tracing off — the default in tests — that is nothing, and the column
    is NULL rather than a fabricated value.
    """
    run = await RunRepository(db_session, actor).create_run(goal="untraced", request_id="r")
    await enqueue(db_session, run.id)

    stored = (
        await db_session.execute(
            text("SELECT trace_parent FROM runs WHERE id = :id"), {"id": str(run.id)}
        )
    ).scalar_one()
    assert stored is None


async def test_a_run_enqueued_without_tracing_claims_fine(
    db_session: AsyncSession, actor: Actor
) -> None:
    """trace_parent is nullable and the worker falls back to a root span. A run
    enqueued with tracing off must not become unclaimable.
    """
    run_id = await queue_a_run(db_session, actor, "untraced")
    await db_session.execute(
        text("UPDATE runs SET trace_parent = NULL WHERE id = :id"), {"id": str(run_id)}
    )

    claimed = await claim_next_run(db_session, "worker-1")

    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.trace_parent is None
