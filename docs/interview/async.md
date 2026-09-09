# Interview — Asynchronous Execution (V0.8)

**Advance gate: V0.9 does not begin until these can be answered unaided.**

The densest distributed-systems content in the project. Also the milestone with the most
opportunities to overclaim, so note what is *not* said: this is multiple processes on one machine
sharing one database. **Not a distributed system.**

---

### What does `SKIP LOCKED` actually do, and what breaks without it?

`FOR UPDATE` takes a row lock. Without `SKIP LOCKED`, a second worker running the same claim query
**blocks** waiting for the row the first worker locked — so N workers serialise into one, which is
the exact opposite of the intent. Worse, it looks like it works: jobs get processed, just never
concurrently.

`SKIP LOCKED` tells Postgres to step over locked rows and take the next free one.

Subtlety worth knowing: it gives you *a* row, not *the first* row. Under contention the `ORDER BY`
is a preference, not a guarantee.

### Why is the claim an `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`?

Because the select-and-lock and the status change must be **one statement in one transaction**.
Selecting a queued run and then updating it separately leaves a window where two workers both saw
it as QUEUED.

This is also what makes a dead worker recoverable *without recovery code*: the lock is released by
the database when the connection dies, and the row's state is whatever the transaction committed —
never a half-claimed limbo.

### Why exactly-once delivery, and why can't you have it?

You can't. A worker can finish a run and die before recording that it finished. The visibility
timeout then makes the run claimable again and it executes twice. No amount of care removes that
window — it is the two-generals problem wearing work clothes.

What is available is **at-least-once**, and the correct response is **idempotent work**, not a
stronger delivery promise.

AMOS's honest position: every tool is read-only, so re-execution wastes tokens and corrupts
nothing. The one exception is `remember_fact` — a duplicated run could store the same fact twice,
which supersession makes harmless **by luck rather than by design**. That is written down as a gap,
not presented as a solution.

### How does a worker crash get recovered?

The visibility timeout. A run left `RUNNING` longer than the timeout is presumed abandoned and
returned to `QUEUED`. **Nothing has to detect that the worker died** — only that a run has been
held too long.

Demonstrated live: worker A claimed a run, was killed with `SIGKILL` (no cleanup possible), the run
sat in `RUNNING` with `claimed_by` naming a dead process, and worker B swept, reclaimed and
completed it. `attempt_count` went to 2, so the retry is visible in the data.

The trade the timeout admits: **a worker that is merely slow can have its run stolen and executed
twice.** That is why the default is generous (600s) — the cost of waiting is latency, the cost of
being too aggressive is duplicate work.

### What stops one bad run from consuming the whole queue?

`attempt_count < max_attempts` in the claim query. A run that crashes its worker every time would
otherwise be reclaimed forever — a poison message that starves every good run behind it.

Once the budget is spent, `give_up()` marks it FAILED with the reason.

### Why claim at run level rather than task level?

A run is what a client submits and polls, and a run's internal task concurrency is already handled
by `asyncio.gather` inside the executor. Distributing individual tasks would mean distributing the
executor — a much larger change for no current benefit.

**This corrected an earlier mistake, and it is the more useful half of the answer.** V0.4 added
`tasks.claimed_at` and a partial claimable index "because adding a column later to a table with
rows is a migration". The reasoning was fine; the guess was wrong — the granularity was never
right. V0.8 removed both rather than carry schema documenting a plan that had been abandoned.

A good demonstration of why the project avoids speculative work: the cost was not the column, it
was that it looked like a decision.

### Why polling instead of `LISTEN`/`NOTIFY`?

`LISTEN`/`NOTIFY` would remove the poll-interval latency floor. It also adds a second mechanism to
reason about: a dedicated connection, reconnect handling, and notifications that are simply lost if
nobody is listening at that instant — so you need the polling fallback anyway for correctness.

Paying that complexity to save latency nobody is measuring is the wrong trade today. Reconsider if
poll latency becomes user-visible.

### Why 202 and not 200?

202 Accepted means the work has been *accepted*, not *done*. Returning 200 would tell the client
the goal completed when it has not started. The `Location` header points at
`/v1/runs/{id}` so the client does not construct the polling URL itself.

Measured: **54 ms**, with no LLM call in the request path at all.

### Why does execution happen outside a transaction?

Same reasoning as V0.3, and it matters more here. A run can take minutes; a transaction holds a
pooled connection for its whole lifetime. Holding one across execution would starve every other
worker on a pool of five.

So: one short transaction to claim, execution with no transaction open, one short transaction to
record.

### Why does the worker swallow every exception?

A worker that dies on one bad run stops draining the queue entirely — one poison message becomes a
total outage. It catches `AmosError` *and* bare `Exception`, records the failure, and continues.

The bound that keeps that safe is the attempt ceiling: swallowing errors without one would mean
retrying a broken run forever.

---

## What V0.8 does NOT demonstrate

- **Not a distributed system.** Multiple processes on one machine, one database. Do not say
  otherwise
- **No `LISTEN`/`NOTIFY`** — polling only
- **No priority, fairness or starvation protection** between clients
- **No dead-letter queue** — given-up runs are marked FAILED and nothing collects them
- **No heartbeats.** Liveness is inferred from `claimed_at` alone, which is why the timeout must be
  generous
- **No graceful shutdown.** `SIGTERM` mid-run relies on the visibility timeout, exactly like a crash
- **Task-level idempotency keys are not implemented** — safety currently rests on tools being
  read-only
