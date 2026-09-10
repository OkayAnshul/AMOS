# V0.8 — Asynchronous execution

**+994 lines.** The densest distributed-systems content in the project — and the milestone with the
most opportunities to overclaim.

Say what this is before you build it: **multiple processes on one machine sharing one database. Not
a distributed system.**

---

## Where you are

Specialised agents planning and executing goals, all inside the HTTP request.

## The problem

A multi-step goal takes tens of seconds. That connection is held the whole time, a client
disconnect loses the work, and a process crash mid-run leaves nothing behind but a row stuck in
`RECEIVED`.

## What you will have at the end

`POST /v1/goals/async` returning **202 in ~76ms** with no LLM call in the request path, and a worker
that can be **killed with `SIGKILL` mid-run** while another finishes the job.

---

## The mechanism, in one statement

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

Understand every part before you type it.

**`FOR UPDATE`** takes a row lock. **Without `SKIP LOCKED`, a second worker BLOCKS** on the row the
first holds — so N workers serialise into one. And it still *looks* like it works: jobs get
processed, just never concurrently.

**`SKIP LOCKED`** steps over locked rows and takes the next free one.

**One statement, one transaction** — the select-and-lock and the status change are atomic, so two
workers cannot both see a run as `QUEUED`. This is also what makes a dead worker recoverable
*without recovery code*: the database releases the lock when the connection dies, and the row is
never half-claimed.

**Subtlety worth knowing:** `SKIP LOCKED` gives you *a* row, not *the first* row. Under contention
the `ORDER BY` is a preference, not a guarantee.

---

## Decisions you are making here

### Postgres, not a broker

You already have Postgres and already persist runs. Celery would add a broker and a framework to
solve a problem the existing database solves at this scale, and split job state across two systems
— the same dual-write objection that chose pgvector.

### Claim at run level, not task level

A run is what a client submits and polls, and a run's internal task concurrency is already handled
by `asyncio.gather` inside the executor. Distributing individual tasks would mean distributing the
executor — a far larger change for no current benefit.

⚠️ **If you added `tasks.claimed_at` at V0.4, remove it now.**

<details><summary>Why</summary>

The original added it there "because adding a column later to a populated table is a migration".
The reasoning was sound and the guess was wrong — the granularity was never right, and it was never
read by any code path.

Carrying it would mean schema documenting an abandoned plan. **A speculative column is worse than a
missing one**, because a column in a schema looks like a decision somebody made for a reason.
</details>

### Polling, not `LISTEN`/`NOTIFY`

`LISTEN`/`NOTIFY` would remove the poll-interval latency floor. It also adds a dedicated connection,
reconnect handling, and notifications that are simply **lost if nobody is listening at that
instant** — so you need the polling fallback anyway for correctness.

Paying that complexity to save latency nobody is measuring is the wrong trade today.

---

## Delivery semantics — decide this before you write code

**Exactly-once is not available, and you must not claim it.**

A worker can finish a run and die before recording that it finished. The visibility timeout then
makes it claimable and it executes twice. No amount of care removes that window.

What you get is **at-least-once**, and the correct response is **idempotent work**, not a stronger
promise.

Your honest position at this point:

| | |
|---|---|
| Every tool is read-only | ✅ re-execution wastes tokens, corrupts nothing |
| `remember_fact` writes | ⚠️ a duplicate could store the same fact twice — supersession makes that harmless **by luck, not design** |
| Task-level idempotency keys | ❌ not implemented |

**Write that middle row down.** It is exactly the kind of thing that is comfortable to leave
unmentioned because nothing is currently broken by it.

---

## Build order

### 1. Migration — run-level claim columns

```sql
ALTER TABLE runs ADD COLUMN claimed_at TIMESTAMPTZ;
ALTER TABLE runs ADD COLUMN claimed_by VARCHAR(64);
ALTER TABLE runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_runs_claimable ON runs (status, created_at) WHERE status = 'QUEUED';
```

**Partial index**, because `QUEUED` runs are a small and shrinking fraction of all runs — an index
over every run ever executed would be mostly dead weight on the hot path.

**`claimed_by` matters more than it looks.** A stuck run should name its owner rather than being
anonymous.

---

### 2. `src/amos/worker/queue.py` (~200 lines)

```python
class RunStatus:
    RECEIVED; QUEUED; RUNNING; COMPLETED; PARTIALLY_COMPLETED; FAILED

DEFAULT_VISIBILITY_TIMEOUT = 600

async def claim_next_run(session, worker_id, *, max_attempts=3) -> ClaimedRun | None: ...
async def reclaim_abandoned_runs(session, *, visibility_timeout=600) -> int: ...
async def enqueue(session, run_id) -> None: ...
async def give_up(session, run_id, reason) -> None: ...
```

**`reclaim_abandoned_runs` is the whole crash-recovery mechanism.** A run left `RUNNING` past the
timeout is presumed abandoned and returned to `QUEUED`.

**Nothing detects that a worker died.** Only that a run has been held too long. That is the entire
design, in about ten lines of SQL.

**The failure it admits, stated plainly:** a worker that is merely *slow* can have its run stolen
and executed twice. The default is generous (600s) because the cost of waiting is latency and the
cost of being aggressive is duplicate work.

**`give_up` prevents poison messages.** A run that crashes its worker every time would be reclaimed
forever, quietly consuming the queue. `attempt_count < max_attempts` in the claim query stops it
being picked up; `give_up` marks it FAILED with the reason.

Without that ceiling, one bad run starves every good one.

**Test first — these need a real database, because the guarantees ARE the SQL:**

| Test | Why |
|---|---|
| **concurrent workers claim disjoint runs** | the test that fails without `SKIP LOCKED` |
| claiming is atomic with the status change | the recoverability property |
| an abandoned run is reclaimed | crash recovery |
| **a healthy run is not stolen** | a timeout too aggressive causes the duplicates it exists to fix |
| a reclaimed run shows `attempt_count = 2` | the retry is visible in the data |
| an exhausted run stops being claimed | poison messages |

The concurrency test is the one worth writing carefully — spawn several claimers with
`asyncio.gather` and assert the claimed ids are **disjoint** and **complete**.

---

### 3. `src/amos/worker/runner.py` (~159 lines)

```python
class Worker:
    async def run_once(self) -> bool:
        """Claim and execute at most one run. Returns whether work was found."""
        # claim            (transaction)
        # execute          (NO transaction — a run can take minutes)
        # record outcome   (transaction)

    async def sweep(self) -> int: ...
    async def run_forever(self, *, sweep_every=30) -> None: ...
```

Deliberately boring. All the interesting guarantees live in `queue.py`'s SQL and in the executor's
state machine. **A worker that adds its own cleverness is a worker with its own bugs.**

**Catch every exception, not just `AmosError`.** A worker that dies on one bad run stops draining
the queue entirely — one poison message becomes a total outage. What keeps that safe is the attempt
ceiling; swallowing errors without one means retrying a broken run forever.

**Sleep only when there was nothing to do**, so a backlog drains at full speed rather than one run
per poll interval.

**Execution outside a transaction matters more here than at V0.3.** A run can take minutes; holding
a pooled connection across it would starve every other worker on a pool of five.

---

### 4. The 202 endpoint

```python
@app.post("/v1/goals/async", status_code=202, response_model=QueuedRun)
async def submit_goal_async(payload, response, idempotency_key=Header(None)):
    run_id = await service.enqueue_only(...)
    response.headers["location"] = f"/v1/runs/{run_id}"
    return QueuedRun(run_id=str(run_id), status="QUEUED")
```

**202, not 200.** The work has been *accepted*, not *done*. Returning 200 tells the client the goal
completed when it has not started.

**The `Location` header** points at where the outcome will appear, so the client does not construct
the polling URL itself.

---

## Checkpoint

### First, that it works

```bash
AMOS_ASYNC_ENABLED=true .venv/bin/python -m amos &
curl -s -i -X POST localhost:8000/v1/goals/async -H 'content-type: application/json' \
  -d '{"goal":"What is 23% of 800?"}'
# HTTP/1.1 202 Accepted, location: /v1/runs/<id>, ~76ms, NO LLM call

.venv/bin/python -m amos.worker &
# poll GET /v1/runs/<id> until COMPLETED → 184
```

### Then, the one that matters

```bash
# 1. queue a run, start worker A, wait until status = RUNNING
# 2. kill -9 <worker A>          ← no signal handler, no cleanup, no chance to update anything
# 3. confirm the run is stuck in RUNNING, claimed_by naming a dead process
# 4. start worker B with AMOS_WORKER_VISIBILITY_TIMEOUT=10
# 5. watch: worker.reclaimed → worker.claimed → worker.completed
```

Then check the data:
```sql
select status, claimed_by, attempt_count, result->>'answer' from runs where id = '<id>';
-- COMPLETED | a DIFFERENT worker | 2 | correct answer
```

**`attempt_count = 2` is the point.** The retry is visible in the data, and nothing anywhere
detected that worker A died.

---

## What this unlocks

Nothing structural — V0.9 and V1.0 do not depend on it. But this is the strongest
distributed-systems content you will have, and the demo above is the most convincing thing in the
project to show an engineer.

**Keep the honest list to hand**, because you will be asked: no `LISTEN`/`NOTIFY`, no priority or
fairness, no dead-letter queue, no heartbeats (liveness is inferred from a timestamp alone), no
graceful shutdown (`SIGTERM` mid-run relies on the same timeout as a crash), and **task-level
idempotency keys are not implemented** — re-execution safety currently rests entirely on tools being
read-only.

Next: [`09-observability.md`](09-observability.md) — the smallest milestone in the project, and the
reason it is small is itself the lesson.
