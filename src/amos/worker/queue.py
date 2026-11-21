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

import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from amos.observability import log_event
from amos.telemetry.tracing import current_trace_context

logger = logging.getLogger(__name__)


class RunStatus:
    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    #: Attempts exhausted; the queue has given up. Distinct from FAILED, which
    #: means the run executed and produced a failure — a real outcome. This means
    #: nobody ever got a verdict, and a person should look. Not claimable, because
    #: the claim query only selects QUEUED.
    DEAD_LETTER = "DEAD_LETTER"


#: A run RUNNING longer than this is presumed abandoned by a dead worker.
#: Long enough that a slow-but-alive run is never stolen; short enough that a
#: crash is recovered within a few minutes.
DEFAULT_VISIBILITY_TIMEOUT = 600


@dataclass
class DeadLetteredRun:
    """One give-up, as a reviewer needs to see it."""

    run_id: str
    goal: str
    attempt_count: int
    last_worker: str | None
    reason: str
    dead_lettered_at: str | None


@dataclass
class ClaimedRun:
    run_id: uuid.UUID
    goal: str
    attempt_count: int
    #: W3C traceparent of the request that enqueued this run, if tracing was on.
    trace_parent: str | None = None
    #: Whose run this is. A worker acts **as the owner**, never unscoped —
    #: otherwise the one component that touches every user's runs would be
    #: the one component with no isolation (ADR-013).
    user_id: uuid.UUID | None = None
    #: Per-run feature switches, applied by the worker (ADR-016).
    options: dict[str, bool] | None = None


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
         RETURNING id, goal_text, attempt_count, trace_parent, user_id, options
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
    return ClaimedRun(
        run_id=row.id,
        goal=row.goal_text,
        attempt_count=row.attempt_count,
        trace_parent=row.trace_parent,
        user_id=row.user_id,
        options=row.options,
    )


async def reclaim_abandoned_runs(
    session: AsyncSession,
    *,
    visibility_timeout: int = DEFAULT_VISIBILITY_TIMEOUT,
    max_attempts: int = 3,
) -> int:
    """Return runs whose worker died to the queue.

    This is what makes a worker crash survivable. A run stuck in RUNNING past the
    visibility timeout is assumed abandoned and becomes claimable again.

    The timeout is a heuristic, and it is worth being clear about the failure it
    admits: a worker that is merely *slow* rather than dead can have its run
    stolen and executed twice. That is at-least-once delivery, and the mitigation
    is idempotent work, not a cleverer timeout.

    **A run abandoned on its final attempt is dead-lettered here** (V1.6). Before,
    it went back to QUEUED — where the claim query, which only takes runs below
    the attempt ceiling, could never pick it up, and `give_up` never ran because
    it lives in the failure handler of the worker that had just died. The run sat
    in QUEUED forever, counted in the queue depth and visible nowhere else.
    """
    params = {"running": RunStatus.RUNNING, "timeout": visibility_timeout, "max": max_attempts}
    exhausted = await session.execute(
        sql_text(
            """
            UPDATE runs
               SET status = :dead, completed_at = now(),
                   error = CAST(:error AS jsonb)
             WHERE status = :running
               AND claimed_at < now() - make_interval(secs => :timeout)
               AND attempt_count >= :max
         RETURNING id
            """
        ),
        {
            **params,
            "dead": RunStatus.DEAD_LETTER,
            "error": json.dumps(
                {
                    "type": "AttemptsExhausted",
                    "message": f"worker lost on the final attempt ({max_attempts} of "
                    f"{max_attempts})",
                }
            ),
        },
    )
    dead = [row.id for row in exhausted]
    for run_id in dead:
        log_event(
            logger, "worker.dead_lettered", run_id=str(run_id), reason="lost on final attempt"
        )

    result = await session.execute(
        sql_text(
            """
            UPDATE runs
               SET status = :queued, claimed_at = NULL, claimed_by = NULL
             WHERE status = :running
               AND claimed_at < now() - make_interval(secs => :timeout)
               AND attempt_count < :max
         RETURNING id
            """
        ),
        {**params, "queued": RunStatus.QUEUED},
    )
    reclaimed = [row.id for row in result]
    if reclaimed:
        log_event(logger, "worker.reclaimed", count=len(reclaimed))
    return len(reclaimed) + len(dead)


async def enqueue(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Mark a recorded run as ready for a worker.

    The current trace context is captured here rather than at creation, because
    here is where the work crosses a process boundary — this is the last moment
    the submitting request and the run are in the same trace.
    """
    await session.execute(
        sql_text(
            "UPDATE runs SET status = :queued, trace_parent = :trace_parent WHERE id = :run_id"
        ),
        {
            "queued": RunStatus.QUEUED,
            "trace_parent": current_trace_context(),
            "run_id": str(run_id),
        },
    )


async def queue_depth(session: AsyncSession) -> int:
    result = await session.execute(
        sql_text("SELECT count(*) FROM runs WHERE status = :queued"),
        {"queued": RunStatus.QUEUED},
    )
    return int(result.scalar_one())


async def give_up(session: AsyncSession, run_id: uuid.UUID, reason: str) -> None:
    """Dead-letter a run after exhausting its attempts.

    Without this, a run that crashes its worker every time would be reclaimed
    forever — a poison message quietly consuming the whole queue.

    The status is DEAD_LETTER rather than FAILED so the two are distinguishable:
    a FAILED run got a verdict, and this one never did. Before V1.1 both landed
    in FAILED, which meant the queue's give-ups were indistinguishable from
    ordinary failures and nothing collected them for review.
    """
    await session.execute(
        sql_text(
            "UPDATE runs SET status = :status, error = CAST(:error AS jsonb), "
            "completed_at = now() WHERE id = :run_id"
        ),
        {
            "status": RunStatus.DEAD_LETTER,
            # json.dumps, not an f-string: `reason` carries an exception message,
            # and a single quote or backslash in it would previously have
            # produced malformed JSON that Postgres rejected — turning a
            # give-up into a crash, in the path that exists to handle crashes.
            "error": json.dumps({"type": "AttemptsExhausted", "message": reason}),
            "run_id": str(run_id),
        },
    )
    log_event(logger, "worker.dead_lettered", run_id=str(run_id), reason=reason)


async def list_dead_letter(
    session: AsyncSession, *, owner: uuid.UUID, limit: int = 50
) -> list[DeadLetteredRun]:
    """One user's runs the queue gave up on, newest first.

    A dead-letter queue nobody can read is a status column. This is the read.

    `owner` is required, not optional: until V1.6 this query had no owner filter
    and the endpoint ignored its actor, so every user saw every user's failed
    goals — the silent failure ADR-013 describes, in a query written as raw SQL
    outside the scoped repository.
    """
    result = await session.execute(
        sql_text(
            "SELECT id, goal_text, attempt_count, claimed_by, error, completed_at "
            "FROM runs WHERE status = :status AND user_id = :owner "
            "ORDER BY completed_at DESC NULLS LAST LIMIT :limit"
        ),
        {"status": RunStatus.DEAD_LETTER, "owner": str(owner), "limit": limit},
    )
    return [
        DeadLetteredRun(
            run_id=str(row.id),
            goal=row.goal_text,
            attempt_count=row.attempt_count,
            last_worker=row.claimed_by,
            reason=(row.error or {}).get("message", ""),
            dead_lettered_at=row.completed_at.isoformat() if row.completed_at else None,
        )
        for row in result
    ]


async def queue_overview(session: AsyncSession) -> dict[str, object]:
    """Queue health for the system panel (V1.7). Counts and worker ids only —
    no goals — so it is not owner-scoped: the queue is the server's, not a user's.

    There are no heartbeats, so "a worker is alive" is inferred from the last
    claim, and the panel says so.
    """
    row = (
        await session.execute(
            sql_text(
                "SELECT count(*) FILTER (WHERE status = :queued) AS queued, "
                "count(*) FILTER (WHERE status = :running AND claimed_by IS NOT NULL) AS running, "
                "max(claimed_at) AS last_claim, "
                "min(claimed_at) FILTER (WHERE status = :running) AS oldest_running "
                "FROM runs"
            ),
            {"queued": RunStatus.QUEUED, "running": RunStatus.RUNNING},
        )
    ).one()
    workers = [
        r.claimed_by
        for r in await session.execute(
            sql_text(
                "SELECT DISTINCT claimed_by FROM runs WHERE claimed_at > now() - interval '1 hour' "
                "AND claimed_by IS NOT NULL ORDER BY claimed_by"
            )
        )
    ]
    return {
        "queued": row.queued,
        "running": row.running,
        "last_claim_at": row.last_claim.isoformat() if row.last_claim else None,
        "oldest_running_since": row.oldest_running.isoformat() if row.oldest_running else None,
        "workers_seen_last_hour": workers,
    }


async def count_dead_letter(session: AsyncSession, *, owner: uuid.UUID) -> int:
    result = await session.execute(
        sql_text("SELECT count(*) FROM runs WHERE status = :status AND user_id = :owner"),
        {"status": RunStatus.DEAD_LETTER, "owner": str(owner)},
    )
    return int(result.scalar_one())
