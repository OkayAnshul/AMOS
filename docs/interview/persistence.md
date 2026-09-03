# Interview — Persistence & Tracing (V0.3)

**Advance gate: V0.4 does not begin until these can be answered unaided.**

---

### Why was persistence built before the planner, when the original plan had it last?

A planner's output *is* state. The moment a goal decomposes into a task graph, you have
distributed task state — you just have it in memory where it cannot be inspected, replayed or
resumed. A multi-step plan that fails halfway and leaves nothing behind is undebuggable: the only
evidence is whatever reached a log line.

It is also the cheaper order. Retrofitting durability at V0.6 would have meant touching three
milestones of code that assumed in-memory state. Building it first means the planner is *born*
durable. ADR-002.

### Why is the run row written before execution rather than after?

So that a crash leaves evidence. If the row were written after the agent returned, a run that
hung, timed out, or killed the process would leave no trace at all — and those are precisely the
runs worth investigating. Writing first means the row exists in `RECEIVED` and is updated to
`COMPLETED` or `FAILED`.

Tested by `test_run_is_recorded_before_execution`.

### Why does execution happen outside the transaction?

Because an LLM call takes seconds, and a transaction holds a pooled connection for its whole
lifetime. Wrapping the agent call in a transaction would tie up a connection per in-flight
request; with `pool_size=5`, six concurrent goals would deadlock on connection acquisition while
doing nothing but waiting on the network.

So there are three short transactions — check idempotency, create the run, record the outcome —
with the slow work between them.

### What does an idempotency key actually protect against?

A client that times out and retries. Without a key, the retry is an entirely new run: the agent
executes again, tokens are spent again, and — once tools can write — side effects happen twice.
With one, the second submit finds the existing run and returns it.

`test_idempotent_resubmit_returns_the_original_run` asserts the part that matters: `agent.calls
== 1`. Returning the same id while secretly re-running would be worse than useless.

Note what this is *not*: it does not make the agent itself idempotent, and it does not handle
two identical requests arriving concurrently before either has committed. That is a V0.8 problem,
when workers claim tasks.

### Why is `run_id` on `llm_calls` and `tool_calls` when `step_id` already links them?

Deliberate denormalisation. Assembling a run trace is the most common read in the system, and
carrying `run_id` makes it one indexed filter per table instead of walking
`runs → steps → llm_calls`. The cost is a redundant column and the obligation to keep it
consistent — paid for by the single query it enables.

`step_id` is nullable because a planning call (V0.4) belongs to a run before any step exists.

### Why `selectinload` rather than lazy loading?

Two reasons, and the first is fatal on its own: lazy attribute access on an async session
**raises** — there is no synchronous point at which SQLAlchemy could issue the query. Even
setting that aside, lazy loading a run's children is N+1 queries for the system's hottest read.
`selectinload` issues one additional SELECT per relationship, regardless of row count.

### Why is `Step` a separate table from `Run` when V0.3 only ever creates one step per run?

Because V0.4 creates many. A retried task has several attempts, and the Task carries the final
outcome while the Steps carry the history of how it got there. Collapsing them would destroy
exactly the evidence retries exist to produce.

Building the table now, with one row per run, means V0.4 adds rows rather than migrating a
schema.

### The V0.3 tests use transaction rollback for isolation. Why not truncate tables?

Each test runs inside a transaction the fixture owns and rolls back afterwards. The code under
test may commit — that commit lands inside the outer transaction, so the test sees its own writes
and nothing else does. Rollback is also far faster than truncating or recreating a schema per
test.

Bonus question: why is the engine fixture function-scoped rather than session-scoped? Because
asyncpg connections are bound to the event loop that created them, and pytest-asyncio gives each
test its own loop. A session-scoped engine hands out connections belonging to a dead loop, which
fails with "attached to a different loop". That cost a debugging cycle.

### Why can AMOS still run with no database at all?

Because "every milestone is runnable" has to survive V0.3. With `AMOS_DATABASE_URL` unset the
app starts, serves goals, and returns `503` with an explanatory message from `/v1/runs/{id}`.
118 of 136 tests pass with nothing running.

The alternative — mandatory infrastructure — would mean a contributor cannot run the project
without a container, and would make the test suite depend on a machine's local state.

### Did persisting `AgentResult` require redesigning it?

No, and that was the bet V0.1 and V0.2 made. `_add_trace_rows` is a mechanical field-by-field
copy from `LLMCallRecord` and `ToolOutcome` into rows, because those types were shaped as
row-precursors from the start.

**One gap was found**, and it is worth stating rather than glossing: `ToolOutcome` recorded a
tool's *result* but not the *arguments* it was called with, so the first trace showed what came
back without what was asked. Fixed by adding `arguments`. The seam was right in shape and 95%
right in content — which is a more honest claim than "it worked perfectly."

---

## What V0.3 does NOT demonstrate

- No planner or decomposition — one step per run, always
- No concurrency: no worker, no queue, no parallel execution. `SKIP LOCKED` arrives at V0.8
- No retries at the task level. The V0.1 repair loop is not task retry
- No retrieval, no vectors yet — pgvector is installed and unused until V0.5
- Backups are a documented `pg_dump` command with **no restore drill**, so not a backup strategy
- Still no authentication. Not deployable publicly
