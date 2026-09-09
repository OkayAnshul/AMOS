"""Job claiming with PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`.

## Why Postgres and not a broker

AMOS already has PostgreSQL and already persists runs (ADR-003). `SKIP LOCKED`
turns that existing table into a correct work queue, and — the part that matters
most — **the claim happens in the same transaction as the state change**, so a
worker that dies mid-run has its row released by the database itself rather than
by recovery code AMOS would have to write and test.

Celery would add a broker and a framework to solve a problem the existing
database solves at this scale, and split job state across two systems: the same
dual-write objection that decided ADR-001.

## What `SKIP LOCKED` actually does

```sql
SELECT id FROM runs WHERE status = 'QUEUED'
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT 1
```

`FOR UPDATE` takes a row lock. Without `SKIP LOCKED`, a second worker running the
same query **blocks** on the row the first has locked — so N workers serialise
into one. `SKIP LOCKED` tells Postgres to step over locked rows and take the next
free one, which is what makes concurrent claiming actually concurrent.

The subtlety worth knowing: `SKIP LOCKED` gives you *a* row, not *the first* row.
Under contention the ordering is a preference, not a guarantee.

## Delivery semantics: at-least-once, not exactly-once

Exactly-once is not available and AMOS does not claim it. A worker can finish a
run and die before recording that it finished; the visibility timeout then makes
the run claimable again and it executes twice.

The correct response is **idempotent work**, not a stronger delivery promise.
AMOS's current honest position: every tool is read-only, so re-execution wastes
tokens but corrupts nothing. `remember_fact` (V0.6) is the one exception, and a
duplicated run could store the same fact twice — which supersession renders
harmless, by luck rather than design. This is recorded in
`docs/17-failure-recovery.md` as a real gap, not a solved problem.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from amos.observability import log_event

logger = logging.getLogger(__name__)


class RunStatus:
    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


#: A run RUNNING longer than this is presumed abandoned by a dead worker.
#: Long enough that a slow-but-alive run is never stolen; short enough that a
#: crash is recovered within a few minutes.
DEFAULT_VISIBILITY_TIMEOUT = 600


@dataclass
class ClaimedRun:
    run_id: uuid.UUID
    goal: str
    attempt_count: int


async def claim_next_run(
    session: AsyncSession, worker_id: str, *, max_attempts: int = 3
) -> ClaimedRun | None:
    """Atomically take one queued run, or return None.

    The `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)` shape is what
    makes this safe: the select-and-lock and the status change are one statement
    in one transaction, so two workers cannot both observe the same row as
    QUEUED.
    """
    result = await session.execute(
        sql_text(
            """
            UPDATE runs
               SET status = :running,
                   claimed_at = now(),
                   claimed_by = :worker,
                   attempt_count = attempt_count + 1
             WHERE id = (
                   SELECT id FROM runs
                    WHERE status = :queued
                      AND attempt_count < :max_attempts
                    ORDER BY created_at
                      FOR UPDATE SKIP LOCKED
                    LIMIT 1
             )
         RETURNING id, goal_text, attempt_count
            """
        ),
        {
            "running": RunStatus.RUNNING,
            "queued": RunStatus.QUEUED,
            "worker": worker_id,
            "max_attempts": max_attempts,
        },
    )
    row = result.first()
    if row is None:
        return None

    log_event(
        logger,
        "worker.claimed",
        run_id=str(row.id),
        worker=worker_id,
        attempt=row.attempt_count,
    )
    return ClaimedRun(run_id=row.id, goal=row.goal_text, attempt_count=row.attempt_count)


async def reclaim_abandoned_runs(
    session: AsyncSession, *, visibility_timeout: int = DEFAULT_VISIBILITY_TIMEOUT
) -> int:
    """Return runs whose worker died to the queue.

    This is what makes a worker crash survivable. A run stuck in RUNNING past the
    visibility timeout is assumed abandoned and becomes claimable again.

    The timeout is a heuristic, and it is worth being clear about the failure it
    admits: a worker that is merely *slow* rather than dead can have its run
    stolen and executed twice. That is at-least-once delivery, and the mitigation
    is idempotent work, not a cleverer timeout.
    """
    result = await session.execute(
        sql_text(
            """
            UPDATE runs
               SET status = :queued, claimed_at = NULL, claimed_by = NULL
             WHERE status = :running
               AND claimed_at < now() - make_interval(secs => :timeout)
         RETURNING id
            """
        ),
        {
            "queued": RunStatus.QUEUED,
            "running": RunStatus.RUNNING,
            "timeout": visibility_timeout,
        },
    )
    reclaimed = [row.id for row in result]
    if reclaimed:
        log_event(logger, "worker.reclaimed", count=len(reclaimed))
    return len(reclaimed)


async def enqueue(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Mark a recorded run as ready for a worker."""
    await session.execute(
        sql_text("UPDATE runs SET status = :queued WHERE id = :run_id"),
        {"queued": RunStatus.QUEUED, "run_id": str(run_id)},
    )


async def queue_depth(session: AsyncSession) -> int:
    result = await session.execute(
        sql_text("SELECT count(*) FROM runs WHERE status = :queued"),
        {"queued": RunStatus.QUEUED},
    )
    return int(result.scalar_one())


async def give_up(session: AsyncSession, run_id: uuid.UUID, reason: str) -> None:
    """Mark a run permanently failed after exhausting its attempts.

    Without this, a run that crashes its worker every time would be reclaimed
    forever — a poison message quietly consuming the whole queue.
    """
    await session.execute(
        sql_text(
            "UPDATE runs SET status = :failed, error = CAST(:error AS jsonb), "
            "completed_at = now() WHERE id = :run_id"
        ),
        {
            "failed": RunStatus.FAILED,
            "error": f'{{"type": "AttemptsExhausted", "message": "{reason}"}}',
            "run_id": str(run_id),
        },
    )
    log_event(logger, "worker.gave_up", run_id=str(run_id), reason=reason)
