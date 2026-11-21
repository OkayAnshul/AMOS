# Interview — The live run record and the read API (V1.7)

**Advance gate: V1.8 does not begin until these can be answered unaided.**

V1.7 makes a run's stored record complete enough to draw the run — its graph, its lanes, every
call in order, every decision — while it runs and after. ADR-015 and ADR-016.

---

### What could `GET /v1/runs/{id}` not tell you before V1.7?

Five things, each found by asking "could the demo page draw this?":
- **Order.** Calls were batch-inserted when the run finished, in one transaction, so they shared
  one `created_at` and came back in arbitrary order.
- **Attribution.** No call said which task, which agent, or what for — a planner call and a
  critic call were indistinguishable rows.
- **Decisions.** Routing decisions and critic verdicts were never stored.
- **Cost.** A delegate's calls were discarded, so `total_tokens` under-counted every delegated run;
  failed task attempts and failed runs stored no calls at all.
- **Liveness.** Nothing was visible until the end, and task rows recorded only terminal states.

### How does a model call know which task and agent it belongs to?

A contextvar, `CallContext` — purpose, agent, task, task attempt, run attempt — changed only
through `call_scope(...)`, which **sets and resets**. Each seam opens a scope: the orchestrator
for the planner and synthesis, the executor at the top of each task, the team for router,
specialist, critic and revision, the delegate tool for the delegate. The run id is *not* in it: it
already has one source (`get_current_run_id`), and a second copy is a second thing to keep in step.

### Why set-and-reset, rather than just setting the contextvar?

Because `asyncio.wait_for` runs its coroutine **in the same task** on Python 3.14 and copies no
context. A bare `.set()` inside an agent run would leak back to its caller, and every later call
would carry the wrong agent and purpose. `asyncio.gather` *does* copy context per child, so a
scope opened inside each gathered task coroutine stays with it — which is why the executor opens
it inside `_run_task`, not in `execute`. Both facts have tests (`test_call_context.py`).

### Where are calls recorded, and why there?

Model calls by `RecordingProvider`, which sits **inside** `RetryingProvider`, so every real
attempt is a row — a retried 503 is two rows, one with its error. Recorded outside the retry, the
retry the page exists to show would be invisible. Tool calls at `ToolUsingAgent._invoke`, the one
point every outcome passes through, NOT_FOUND included; it covers delegates, revisions and the
reconciler's agent with no extra code.

### Why one transaction per call? Isn't that a lot of writes?

It is tens of small writes per run against a model round-trip of seconds each — noise. The
precedent is V1.1's `RunProgress`: short transactions around SQL only. The property that makes it
safe is that **no model call ever runs inside an open session**, so a write per call never nests a
connection checkout and cannot exhaust the pool.

### Why typed call tables plus an event table, not one event log for everything?

The trace endpoint, its schemas and the eval's counts all read typed `llm_calls` and `tool_calls`
rows. Moving calls into JSON events would break them or duplicate them. So calls keep their
tables, and `run_events` holds only what has no home: routing, verdicts, task starts and retries,
delegation. **One Postgres sequence** is the default for a `seq` column on all three — a total
order across them.

### Why does the page poll the whole run rather than "everything after seq N"?

A sequence value is assigned at **insert**, not at commit. Two tasks running concurrently can
commit out of order, so a client that had seen seq 12 could miss a seq 11 that committed a moment
later — forever. A run is a few hundred rows; fetching all of it is simpler than being clever and
wrong.

### The end-of-run batch insert still exists. Why doesn't it double every call?

`_add_trace_rows` checks, per table and per run, whether live rows already exist, and skips if so.
Not a global switch: test doubles and agents built without `build_provider` do not record live,
and they still need the batch.

### Why is `total_tokens` now a SUM over stored calls?

Because the in-memory total was wrong in three known ways — failed task attempts, delegates and
earlier worker attempts were missing. The stored rows are what was actually spent. Consequence:
the figure is not one series with earlier runs'.

### How do per-run options work, and why "off only"?

`options` on a goal switches planning, multi-agent, critic, delegation or memory **off** for one
run; switching on what the server has off is a 422. A server flag is the operator's decision about
cost and capability — if a request could override it, the environment file would be advisory.
Options are stored on the run, so the worker applies them. A run with options gets its own agent
built from the shared provider and tools; constructors do no I/O, so that costs allocation only.

### What does `memory: false` have to switch off besides the memory tools?

Episodic recording after the run, and the memory reconciler — which has its own `remember_fact`
and would write memory anyway. Missing either would make the option a lie.

### Why is the quota meter not owner-scoped when everything else is?

A free-tier quota belongs to the API key, not to an AMOS user, so the honest number is everything
this server spent. It returns counts only, no content, and lives in its own module rather than as
an exception inside a scoped one. It is a **floor**: calls outside a run and every embedding call
are not recorded, and the response says so.

### How are uploads kept from leaking or clobbering the corpus?

They are owned (`documents.user_id`), stored as `uploads/<name>`, and retrieval already matches
`user_id IS NULL OR user_id = :owner` (fixed in V1.6). Replacement on re-upload is scoped to the
owner, so naming a file `00-vision.md` replaces nothing in the system corpus. The content hash is
namespaced by owner, because the hash is unique across the table and two users uploading the same
text are two documents.

### What did the survey find that wasn't a missing feature?

Two definitions of `RunStatus` that disagreed (one had `EXECUTING`, never written; neither was
complete); the sync path never writing `RUNNING`; the worker recording no episodes and no metrics.

---

## What this milestone does *not* claim

- Not streaming: the page polls. Server-sent events only if polling proves inadequate.
- Not a complete cost record: calls outside runs and embeddings are unrecorded.
- Not one series: `total_tokens` changed definition.
- Not authorization: options and uploads are per user, not per role.
- Recording can fail silently by design — a lost row is logged, never raised.
