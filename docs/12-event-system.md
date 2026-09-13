# 12 — Asynchronous Execution

**Written at V0.8, extended at V1.1.**

> **This document describes a PostgreSQL job queue, not a message broker.** There is no Kafka, no
> NATS, no Redis and no Celery in AMOS, none is planned, and none should be claimed. The file is
> numbered as the "event system" because the roadmap reserved that slot at Phase 0; what actually
> earned its place is a queue.

## The shape

```
POST /v1/goals/async ──▶ 202 Accepted + run_id   (54 ms measured, no LLM call)
                          │
                     runs.status = QUEUED
                          │
                    ┌─────┴─────┐
                 worker A    worker B      SELECT ... FOR UPDATE SKIP LOCKED
                    │           │
              executes the orchestrator, writes the outcome
                          │
GET /v1/runs/{id} ──▶ QUEUED → RUNNING → COMPLETED / FAILED
```

## Why PostgreSQL and not a broker

AMOS already has PostgreSQL and already persists runs (ADR-003). `SKIP LOCKED` turns that table
into a correct work queue — and the property that matters most is that **the claim and the state
change are one statement in one transaction**, so a worker that dies has its row released by the
database rather than by recovery code AMOS would have to write and test.

Celery would add a broker and a framework to solve a problem the existing database already solves
at this scale, and split job state across two systems: the same dual-write objection that decided
ADR-001.

## What `SKIP LOCKED` actually does

```sql
UPDATE runs
   SET status = 'RUNNING', claimed_at = now(), claimed_by = :worker,
       attempt_count = attempt_count + 1
 WHERE id = (
       SELECT id FROM runs
        WHERE status = 'QUEUED' AND attempt_count < :max_attempts
        ORDER BY created_at
          FOR UPDATE SKIP LOCKED
        LIMIT 1
 )
RETURNING id, goal_text, attempt_count
```

`FOR UPDATE` takes a row lock. **Without `SKIP LOCKED`, a second worker running this query blocks
on the row the first has locked** — so N workers serialise into one, which is the opposite of the
intent. `SKIP LOCKED` steps over locked rows and takes the next free one.

One subtlety: `SKIP LOCKED` gives you *a* row, not *the first* row. Under contention the
`ORDER BY` is a preference, not a guarantee.

## Claiming at run level, not task level

A run is what a client submits and polls, and a run's internal task concurrency is already handled
by `asyncio.gather` inside the executor. Distributing individual tasks across workers would mean
distributing the executor, which is a far larger change for no current benefit.

**This corrected an earlier guess.** V0.4 added `tasks.claimed_at` and a partial claimable index
"because adding a column later to a table with rows is a migration". The reasoning was sound and
the guess was wrong: the granularity was never right. V0.8 **removed both** rather than carry
schema documenting an abandoned plan.

## Crash recovery: the visibility timeout

A run left `RUNNING` longer than `AMOS_WORKER_VISIBILITY_TIMEOUT` (default 600s) is presumed
abandoned and returned to `QUEUED`. Nothing has to detect that a worker died — only that a run has
been held too long.

**Since V1.1 the reclaim resumes** rather than restarting: the plan is persisted when it is made
and each task checkpointed as it finishes, so the second worker skips what the first completed and
does not call the planner again (ADR-010). The trace continues too — `runs.trace_parent` carries
the submitting request's W3C context, so a queued run is one trace end to end rather than two
unlinked ones.

**Demonstrated:** worker A claimed a run and was killed with `SIGKILL` (no cleanup, no chance to
update anything). The run sat in `RUNNING` with `claimed_by` naming a dead process. Worker B
started, swept, reclaimed it, and completed it. `attempt_count` went to 2, so the retry is visible
in the data.

The timeout is a heuristic and admits a real failure: **a worker that is merely slow can have its
run stolen and executed twice.**

## Delivery semantics: at-least-once

**Exactly-once is not available, and AMOS does not claim it.** A worker can finish a run and die
before recording that it finished; the visibility timeout then makes it claimable and it executes
twice.

The correct response is **idempotent work**, not a stronger delivery promise. AMOS's honest
position today:

| | Status |
|---|---|
| Every tool is read-only | ✅ so re-execution wastes tokens, corrupts nothing |
| `remember_fact` writes | ⚠️ a duplicate run could store the same fact twice — supersession makes that harmless **by luck, not by design** |
| A reclaimed run resumes rather than re-executing | ✅ **V1.1** (ADR-010) |
| Task-level idempotency keys | ❌ still not implemented |

**V1.1 narrowed this window; it did not close it.** A reclaimed run now skips the tasks that
already succeeded, so the duplicate work is one task rather than a whole run. A worker that dies
*between* finishing a task and checkpointing it will still redo that task — and delivery is still
at-least-once, because that is a property of the world and not of this code.

The `remember_fact` row is unchanged and is still a real gap. It becomes a genuine bug the moment
a tool has side effects, which is why the registry refuses `WRITE` permission.

## Poison messages

A run that crashes its worker every time would be reclaimed forever, quietly consuming the queue.
`attempt_count < max_attempts` in the claim query stops it being picked up, and `give_up()` marks
it `FAILED` with the reason.

Without that ceiling, one bad run starves every good one.

## Why polling rather than `LISTEN`/`NOTIFY`

Postgres has `LISTEN`/`NOTIFY`, which would remove the poll-interval latency floor. Not used
because it adds a second mechanism to reason about — a dedicated connection, reconnect handling,
and notifications lost if nobody is listening — to save latency nobody is currently measuring.

Reconsider if poll latency becomes user-visible.

## Running it

```bash
podman-compose up -d && .venv/bin/alembic upgrade head
AMOS_ASYNC_ENABLED=true .venv/bin/python -m amos     # the API
.venv/bin/python -m amos.worker                      # one or more workers
```

Workers are stateless and identical; run as many as the quota tolerates.

| Setting | Default | Purpose |
|---|---|---|
| `AMOS_WORKER_POLL_INTERVAL` | 2.0s | sleep when the queue is empty |
| `AMOS_WORKER_MAX_ATTEMPTS` | 3 | poison-message ceiling |
| `AMOS_WORKER_VISIBILITY_TIMEOUT` | 600s | when a RUNNING run is presumed abandoned |

## Not done

- **No `LISTEN`/`NOTIFY`** — polling only
- **No priority or fairness** — strictly oldest-first, and no starvation protection between clients
- ~~No dead-letter queue~~ — **added at V1.1.** A given-up run is now `DEAD_LETTER`, distinct from
  `FAILED`, and readable at `GET /v1/runs/dead-letter`. The distinction is the point: a `FAILED`
  run executed and produced a verdict; a dead-lettered one never got one
- **No worker autoscaling, no heartbeats.** Liveness is inferred from `claimed_at` alone, which is
  why the timeout must be generous
- **No graceful shutdown.** A `SIGTERM` mid-run relies on the visibility timeout, exactly like a
  crash. Draining in-flight work on shutdown would make restarts cheaper
- **Still not a distributed system** — multiple processes on one machine sharing one database
