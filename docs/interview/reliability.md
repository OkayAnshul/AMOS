# Interview — Reliability (V1.1)

**Advance gate: V1.2 does not begin until these can be answered unaided.**

The milestone that stopped crash recovery resting on an accident. Note what is still *not*
claimed: delivery is at-least-once, tasks are not idempotent, and the window for duplicate work
was narrowed rather than closed.

---

### V0.8 already reclaimed a crashed run. What was actually wrong with that?

"Reclaimed" meant **re-executed from the beginning**. Another worker picked the run up and ran it
from scratch.

That was safe only because of a property AMOS does not control for: every registered tool is
read-only. The registry refuses `WRITE` and `DESTRUCTIVE` permissions, so re-running a run wasted
tokens and corrupted nothing. There was already one exception — `remember_fact` writes, and a
duplicate store is harmless *because supersession makes it a no-op*, which is luck rather than
design.

So the recovery story was: correct, but for a reason that would stop being true the first time
anyone added a tool with a side effect.

### Why was resumption impossible before V1.1? Be specific.

**Nothing was persisted until the run finished.** `RunRepository.record_success` wrote the tasks,
steps, LLM calls and tool calls in one batch at the end. A run killed mid-execution left a `runs`
row and nothing else.

So there was no record of which tasks had already succeeded, and therefore nothing to resume
*from*. This is the part that is invisible from outside: the API exposes `GET /v1/runs/{id}` and
a task DAG, so it looks like task state is being tracked all along. It was only ever written once,
at the end.

### Once you persist task outcomes, why can't you just re-plan and skip the ones that succeeded?

Because **the planner is an LLM**. Call it twice with the same goal and you get a different DAG —
different task ids, different descriptions, possibly a different number of tasks. The stored
record of "t1 and t2 succeeded" would no longer refer to anything in the new plan.

The option that fails most interestingly is matching by description: comparing two pieces of
model-generated prose to decide what to skip. That puts control flow on the wrong side of the
project's golden rule — LLMs handle uncertainty, software handles guarantees — and a near-match
would silently skip the wrong task, which is worse than redoing it.

### So what does AMOS do instead?

**The stored task rows *are* the plan.** The plan is persisted when it is validated, before any
task runs. On a later attempt, `PlanStore.load()` returns the rows rebuilt into a `Plan`, plus
`{plan_ref: answer}` for the tasks that already succeeded — and **the planner is not called at
all**.

Side benefit worth mentioning: that removes an LLM call from every reclaim, which on a
20-request/day free tier is a fifth of the day's budget. It is not the reason, though. The reason
is that a second plan would invalidate the first one's record.

### Why is the checkpoint a protocol rather than the executor just writing to the database?

Because the executor has no business knowing a database exists. It already depended on a
`TaskRunner` protocol for the agent; `TaskCheckpoint` is the same shape — one method, implemented
by the persistence layer, faked in tests, and `None` when there is no database.

That is what keeps "every milestone is runnable without infrastructure" true: with no
`AMOS_DATABASE_URL`, both the plan store and the checkpoint are `None` and the orchestrator
behaves exactly as it did at V0.4.

### What happens if a checkpoint write fails?

It is caught and logged, and the run continues.

The reasoning is the same as episodic recording: losing the ability to resume **one task** is a
smaller harm than failing a run whose work is already done and correct. A checkpoint is an
optimisation for a future attempt, not part of the current attempt's correctness.

`record_success` at the end of the run is the backstop — it upserts task rows, so a run that
finishes reconciles any state a failed checkpoint left stale.

### Why did `_add_task_rows` have to become an upsert?

Because rows now usually exist by the time the run completes — the plan store created them and the
checkpoints updated them — and `UNIQUE (run_id, plan_ref)` would reject a second insert.

It upserts rather than skipping the write entirely so that `record_success` stays the backstop
described above.

### Does a resumed task count its tokens again?

No, and this is a deliberate detail. `_resumed_result` carries **no `llm_calls`**. The tokens were
spent on the earlier attempt and counted against it; counting them again would make a resumed run
look more expensive than it was and inflate every total derived from it.

Its `confidence` is `MEDIUM` rather than whatever the original attempt reported, because the
stored row keeps the answer and not the confidence — asserting `HIGH` would be inventing a value
that was never recorded.

### Is exactly-once delivery solved now?

**No.** Exactly-once is not available, and resumption is not an attempt at it.

What changed is the *size* of the duplicate-work window. Before: a crash meant redoing an entire
run. Now: a worker that dies **between finishing a task and checkpointing it** redoes that one
task. The window is narrower and it is still there, because the gap between doing work and
recording that you did it cannot be closed by code on one side of it — that is the Two Generals'
problem, not a missing feature.

### What would task-level idempotency keys add that resumption does not?

They are the more commonly cited "fix", and they are weaker here. An idempotency key makes a
repeat execution *harmless*; it does not avoid it. The work still runs, and on this quota the work
is the expensive part.

They would matter once a tool has side effects, because then "harmless" is a property you have to
enforce rather than inherit from every tool being read-only. That is why it stays on the list.

### Why is `DEAD_LETTER` a different status from `FAILED`?

Because they are different events, and collapsing them loses the one that needs attention.

- `FAILED` — the run executed and produced a failure. Somebody got a verdict.
- `DEAD_LETTER` — the queue gave up. The worker died on it `AMOS_WORKER_MAX_ATTEMPTS` times and
  nobody ever got a verdict.

Before V1.1 both landed in `FAILED`, so give-ups were indistinguishable from ordinary failures and
nothing collected them. That is the difference between a poison-message *ceiling* (which V0.8 had)
and a dead-letter *queue* (which it did not).

It also needs no migration and no second table: `runs.status` is `TEXT`, a dead-lettered run's rows
are identical to any other run's, and the claim query selects only `QUEUED` — so a dead-lettered
run is automatically not claimable.

### Why must `/v1/runs/dead-letter` be declared before `/v1/runs/{run_id}`?

FastAPI matches routes **in declaration order**. With the parameterised route first,
`dead-letter` binds as a `run_id`, fails the UUID check, and returns `422` — an error on a path
that genuinely exists, which is a confusing thing to debug.

The ordering is load-bearing and invisible, so a test pins it rather than a comment asking the
next person to be careful.

### Why is the trace context captured at enqueue rather than when the run row is created?

Because **enqueue is where the work crosses a process boundary**. It is the last moment the
submitting request and the run are in the same trace; after it, the run belongs to whichever
worker claims it, minutes later and in another process.

Before V1.1 a queued run was two unlinked traces: one for the submitting request, one started
fresh by the worker. The run id correlated them if you knew to look, but no tracing backend would
show them as one.

### What happens to a run enqueued while tracing was disabled?

`trace_parent` is `NULL` and the worker opens a **root span** instead of a child.

That is the honest degradation. The column is nullable with no backfill, because runs enqueued
before it existed genuinely have no parent trace and inventing one would be worse than the gap.

### What bug did wiring the trace propagation uncover?

**The worker never put the run id in context.** The synchronous path called
`set_current_run_id`; the worker path only set the request id.

Since both the plan store and the task checkpoint read the run id from context — the same way the
memory tools do, because the orchestrator is built once at startup — V1.1's resumption would have
been inert inside the worker. Which is the only place a reclaim happens, and therefore the only
place resumption matters at all.

A whole feature, fully built and tested, that would have done nothing where it counted. It is the
same shape as the `remember_fact` bug: the capability worked and the wiring did not, and unit
tests could not see it because they construct the pieces directly.

The run id is also **cleared after each run**, because a worker is a long-lived loop and a stale
id would land the next run's checkpoints on the previous run's rows.

### What did the test-isolation failure in this milestone teach?

An integration test called `set_tracer_provider`, which OpenTelemetry honours **once per process**
— later calls are ignored with a warning, not an error. It ran before the telemetry unit tests and
silently blinded five of them: they captured no spans and failed on assertions about spans that
had genuinely been created, just recorded by a provider they did not own.

Two lessons. Process-global state set inside a test is a hazard to every test after it. And: run
the **whole** suite before committing, not the files you touched — the failures were in a
directory the change never opened.

---

## What this milestone does *not* claim

- Not exactly-once. Not close to it, and not trying.
- Not task-level idempotency.
- Not re-planning: a resumed run faithfully re-runs the stored plan, **including a bad one**. That
  is now a deliberate consequence of ADR-010 rather than an accident.
- Still not a distributed system — multiple processes, one machine, one database.
- No heartbeats, no graceful shutdown. Liveness is still inferred from `claimed_at` alone, which
  is why the visibility timeout has to be generous.
