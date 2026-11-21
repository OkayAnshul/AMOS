# 03 — Architecture Decision Records

Every technology in AMOS has a record here, including the ones deliberately *not* adopted.
Each ends with **Reconsider if** — the condition that would make the decision wrong. A decision
without that line is a preference, not an engineering judgement.

---

## ADR-001 — pgvector, not Qdrant

**Date** 2026-09-03 · **Status** Accepted

### Context
AMOS needs vector similarity search for RAG (V0.5) and semantic memory (V0.6). The original
project brief named Qdrant. Expected corpus: thousands to low tens of thousands of chunks —
project documentation and personal notes, not a web-scale index.

### Problem
Dedicated vector database, or vectors inside the relational database AMOS already needs?

### Options
1. **Qdrant** — dedicated Rust vector DB. Excellent at scale: sharding, quantization, rich
   filtering.
2. **pgvector** — a Postgres extension. Vectors live in ordinary tables.
3. **ChromaDB** — fastest prototype path, weakest operational story.

### Decision
**pgvector**, accessed through a `VectorStore` protocol.

### Why
The deciding factor is not performance — at this corpus size both are far past sufficient. It
is **consistency**. With Qdrant, a chunk's row lives in Postgres and its embedding lives in
Qdrant, so every write is a distributed write across two systems with no shared transaction.
Postgres commits, Qdrant fails, and the index now disagrees with the source of truth. Fixing
that properly means an outbox, a reconciliation job, or accepting silent drift.

That is a real distributed-systems problem, and AMOS would be *creating it voluntarily* to
solve a scale problem it does not have. With pgvector, the chunk and its embedding are columns
in the same row, written in the same transaction. The failure mode does not exist.

Secondary: one service to run, back up and restore instead of two.

An earlier draft of this ADR also cited disk pressure (the machine had 9.4 GB free). That
constraint was removed before the decision was finalised, and the decision did not change —
recorded here because a reason that evaporates should not be quietly retained.

### Tradeoffs
- Give up: quantization, distributed sharding, and Qdrant's richer filter engine.
- Give up: direct hands-on experience operating a dedicated vector database.
- Accept: HNSW index builds are slower in pgvector, and vector search competes with OLTP work
  for the same Postgres resources.

### Consequences
- One `docker compose` service at V0.3 serves both relational and vector needs.
- Retrieval joins chunk text, metadata and embedding in one query — no cross-system fan-out.
- A `VectorStore` protocol keeps the swap cheap. This is *not* speculative generality: the test
  suite needs an in-memory implementation regardless, so the abstraction is paid for by V0.5's
  own tests.

### Reconsider if
- Vector count exceeds ~5M, or ANN recall/latency measurably degrades under load
- Metadata filtering becomes complex enough that pgvector's planner mis-costs the query
- Quantization becomes necessary to fit the index in RAM
- Vector search starts starving transactional queries on the same instance

---

## ADR-002 — Persistence and tracing before the planner

**Date** 2026-09-03 · **Status** Accepted

### Context
The original roadmap sequenced tools → planner → RAG → memory → *reliability last*. Reliability
and persistence were to arrive at V0.6, after three milestones of features.

### Problem
When should durable run/step state and an execution-trace endpoint be built?

### Options
1. **Spec order** — planner at V0.3, persistence retrofitted at V0.6.
2. **Persistence first** — durable state at V0.3, planner at V0.4.

### Decision
**Persistence at V0.3, planner at V0.4.**

### Why
A planner's output *is* state. The moment a goal decomposes into a task graph, AMOS has
distributed task state — it simply has it in memory, where it cannot be inspected, replayed or
resumed. A multi-step plan that fails halfway and leaves nothing behind is undebuggable in
practice: the only evidence is whatever happened to reach a log line.

Retrofitting persistence later is also the expensive order. By V0.6 there would be three
milestones of code assuming in-memory state, and adding durability means touching all of it.
Building it first means the planner is *born* durable, and every subsequent milestone inherits
inspectability instead of being retrofitted with it.

### Tradeoffs
- The impressive "planner decomposes a goal" demo slips by one milestone.
- V0.3 is infrastructure-heavy: Docker, Postgres, SQLAlchemy and Alembic all arrive at once,
  and it is the least visually impressive milestone in the roadmap.

### Consequences
- V0.3's demo is `GET /v1/runs/{id}` returning a complete trace — a genuine capability, and the
  answer to the first question a skeptical interviewer asks.
- Retries, idempotency and async execution (V0.8) all become natural extensions of an existing
  durable model rather than new subsystems.

### Reconsider if
An interview or deadline requires demonstrating planning sooner than durability. That is a
legitimate reason to reorder, but it is a *presentation* decision — record it as such rather
than pretending it was an engineering one.

---

## ADR-003 — PostgreSQL `SKIP LOCKED` as the job queue, not Celery/Redis

**Date** 2026-09-03 · **Status** Accepted (revisit at V0.8)

### Context
V0.8 introduces asynchronous execution: goals that take minutes must not block an HTTP request.

### Problem
What claims work for a background worker without losing tasks when a worker dies?

### Options
1. **Celery + Redis** — the conventional Python answer.
2. **PostgreSQL `SELECT … FOR UPDATE SKIP LOCKED`** — atomic job claiming in the existing DB.
3. **Temporal** — durable execution as a managed concern.

### Decision
**`SKIP LOCKED` in PostgreSQL.**

### Why
AMOS already has PostgreSQL and already persists tasks (ADR-002). `SKIP LOCKED` turns that
existing table into a correct work queue: concurrent workers claim disjoint rows atomically,
and because claiming happens in the same transaction as the state change, a crashed worker's
task is released by the database itself rather than by a reaper AMOS has to write.

Celery would add two dependencies (a broker and a framework) to solve a problem the existing
database already solves at this scale, and split task state across two systems — the same
dual-write objection as ADR-001.

Temporal solves this genuinely well and is the right answer at real scale; it is also a large
conceptual surface to adopt for one worker on a laptop.

### Tradeoffs
- Polling rather than push, so there is a latency floor set by the poll interval.
- No fan-out, no scheduled/periodic tasks, no chords or chains — all of which Celery gives free.
- Throughput ceiling is Postgres, which is thousands/sec — far beyond need, but real.

### Consequences
- Zero new infrastructure at V0.8.
- Forces genuine engagement with at-least-once delivery, visibility timeouts and idempotency,
  rather than delegating them to a framework. That is the more valuable thing to understand.

### Reconsider if
- Multiple worker types need independent scaling
- Scheduled or periodic tasks become a requirement
- Poll latency becomes user-visible, or queue throughput approaches Postgres limits
- Workflows need durable multi-day state — at which point Temporal, not Celery

---

## ADR-004 — Modular monolith, not microservices

**Date** 2026-09-03 · **Status** Accepted

### Context
The target architecture has many components: orchestrator, agents, tools, memory, evaluation.
Component diagrams look like service diagrams, and the resemblance is a trap.

### Decision
**One deployable process** with enforced internal module boundaries.

### Why
Microservices solve organisational and operational problems — independent deployment,
independent scaling, team ownership, fault isolation. AMOS has one developer, one machine and
one deployment. It has none of those problems, and would pay the full price: network calls
between components, distributed tracing to understand a single request, partial-failure
handling everywhere, and multi-service local development.

Module boundaries deliver most of the *design* benefit — clear ownership, testable seams —
at none of the operational cost. If a boundary ever needs to become a network boundary, a
well-drawn module is exactly what makes that extraction possible.

### Tradeoffs
- No hands-on distributed-systems operations experience from the deployment topology.
- Boundaries are enforced by discipline and review, not by the compiler or the network. They
  will erode without attention.

### Consequences
- **"Distributed system" is not a claim AMOS may make.** The distributed-systems concepts it
  genuinely exercises — idempotency, at-least-once delivery, retries, visibility timeouts —
  are claimable individually and with evidence.

### Reconsider if
A component needs genuinely independent scaling (e.g. GPU-bound embedding work), or a
component's failure must be isolated from the rest of the system.

---

## ADR-005 — Gemini first, behind a provider protocol

**Date** 2026-09-03 · **Status** Accepted

### Context
AMOS needs an LLM. Long term it should not be captive to one vendor.

### Decision
`google-genai` ≥2.21.0 with `gemini-3.5-flash`, behind an `LLMProvider` protocol. One provider
implemented; the seam for others built immediately.

### Why
Gemini's free tier makes iteration free, which matters more than model quality for a project
whose bottleneck is understanding rather than capability. It has native structured output and
function calling — the two features V0.1 and V0.2 are built on.

The protocol is not speculative: the test suite needs a `FakeProvider` from day one (N-14, free
tier is 15 RPM and tests must not hit the network). The abstraction is therefore paid for by
V0.1's own tests, and multi-provider support arrives as a side effect rather than as
anticipatory design.

### Tradeoffs
- The protocol must express the *intersection* of provider capabilities, or leak vendor
  specifics. Provider-specific features need explicit escape hatches.
- Free tier means rate limits and data-used-for-training terms. No confidential input.

### Consequences
- Model IDs live in configuration, never in code.
- `gemini-embedding-001` is the natural embedding choice at V0.5 (same SDK, same credential).

### Reconsider if
Free-tier limits block development, Gemini's structured-output reliability proves insufficient,
or a task needs a model only another vendor offers.

---

## ADR-006 — No database at V0.1

**Date** 2026-09-03 · **Status** Accepted

### Context
Production systems have databases, and the instinct is to start with one.

### Decision
V0.1 has **no persistence**. PostgreSQL arrives at V0.3, when durable runs are the milestone.

### Why
Nothing in V0.1 needs to survive a restart. A goal comes in, an answer goes out. Adding a
database would mean Docker, a schema, migrations and connection lifecycle management — real
complexity in service of no requirement — and would make V0.1 impossible to run without
infrastructure, undermining "every milestone is runnable".

This is the anti-over-engineering rule applied to AMOS itself. It is easy to state and
uncomfortable to follow, because a database feels like seriousness.

### Tradeoffs
- V0.1 sounds less impressive described out loud.
- V0.3 must introduce persistence across existing code — mitigated by the `AgentResult`
  envelope (docs/02), which is already shaped like the row it becomes.

### Consequences
V0.1 runs with `pip install` and an API key. No Docker, no services.

### Reconsider if
V0.2 needs cross-request state. If so, persistence moves to V0.2 rather than being faked with
a global dictionary.

---

## ADR-007 — Ten documents written, fourteen stubbed

**Date** 2026-09-03 · **Status** Accepted

### Context
The project brief asks for 24 documents before implementation, and separately forbids
meaningless placeholder documentation. Both cannot hold.

### Decision
Write in full only the documents that constrain V0.1–V0.3. The rest are one-line stubs naming
the milestone that will write them and the fact they wait on.

### Why
A RAG architecture document written before a single document has been embedded would specify
chunk size, top-k and an embedding model as *guesses* — and would then read as decisions, get
cited, and constrain later work for no reason. Documentation should record decisions that were
actually made, ideally against evidence.

The stub still carries the architectural intent: a reader sees the full shape of the system and
sees honestly which parts are decided and which are not.

### Tradeoffs
Less impressive at a glance than 24 complete documents.

### Reconsider if
A stub's subject starts influencing implementation before its milestone — that means the
decision is being made implicitly, and it should be written down properly instead.

---

## ADR-008 — Embeddings at 1536 dimensions

**Date** 2026-09-03 · **Status** Accepted (implement at V0.5)

### Context
`gemini-embedding-001` outputs **3072** dimensions by default and supports Matryoshka (MRL)
truncation. pgvector's HNSW and IVFFlat indexes support at most **2000** dimensions for the
`vector` type (4000 for `halfvec`).

### Problem
The default embedding **cannot be indexed** by pgvector. Verified against
<https://github.com/pgvector/pgvector>, not assumed.

### Options
1. `vector(3072)` unindexed — exact search, full scan every query.
2. **MRL-truncate to 1536**, then re-normalise → `vector(1536)`, HNSW-indexable.
3. `halfvec(3072)` — half precision, indexable to 4000 dims.

### Decision
**Truncate to 1536 and re-normalise**, stored as `vector(1536)` with an HNSW index.

### Why
MRL is designed for exactly this: the model is trained so that leading dimensions carry most of
the signal, and Google documents quality loss as small at 1536. Option 1 abandons indexing
entirely. Option 3 is a genuine contender and stays documented as the fallback, but half
precision introduces a second quality variable on top of an unmeasured pipeline — one variable
at a time is easier to reason about.

**Re-normalisation is mandatory**: truncating an L2-normalised vector leaves it un-normalised,
and cosine distance on un-normalised vectors is silently wrong — wrong rankings, no error.

### Tradeoffs
- Some retrieval quality given up versus full 3072 dimensions, quantity unmeasured until V0.5.
- Changing dimensions later means re-embedding the entire corpus.

### Consequences
- Storage: 1536 × 4 bytes ≈ 6 KB per chunk.
- V0.5 must measure recall@k, so this tradeoff is evaluated against data rather than assumed.

### Reconsider if
Measured recall@k at 1536 is materially worse than at 3072 on the golden set — then move to
`halfvec(3072)` and re-measure.

---

## ADR-009 — A task-level timeout, and the ordering of the bounds

**Date** 2026-09-13 · **Status** Accepted

### Context
`TaskState.TIMED_OUT` was declared at V0.4 with legal transitions in and out of it, drawn in
`state.py`'s own ASCII diagram, and **no code could ever produce it**. The executor reached
only `SUCCEEDED` and `FAILED`. The exhaustive state-machine tests all passed, because they
test the transition *table* — the table was correct and nothing exercised that row.

### Problem
Every bound in AMOS below this point is **per call**: 30s on an LLM request, 2–30s on a tool.
A task is a *loop* over those calls (`agent_max_iterations`, default 5), so bounded parts do
not add up to a bounded whole. A task could legitimately run for as long as the loop kept
finding work to do, and nothing would stop it.

### Options
1. **Delete `TIMED_OUT`** and declare per-call bounds sufficient.
2. **A task-level timeout** in the executor, entering `TIMED_OUT`.
3. **Rely on the worker's visibility timeout** to reclaim the whole run.

### Decision
**Option 2.** `asyncio.wait_for` around the runner call, default **300s**, configurable as
`AMOS_TASK_TIMEOUT_SECONDS`.

### Why
Option 1 gives up a containment boundary: a task that hangs *between* calls — or simply loops
to its iteration cap against slow tools — takes the whole run with it, and the run has no
other bound in the synchronous path.

Option 3 is not a substitute. The visibility timeout only exists for **queued** runs, so it
does nothing for `POST /v1/goals`; and it escalates a contained, retryable single-task failure
into re-executing the entire run. Containment belongs at the smallest unit that can be retried
on its own, which is the task.

The value matters less than **the ordering**, which is the real content of this decision:

```
per-call bounds  <  task timeout  <  worker visibility timeout
30s LLM, ≤30s tool      300s                    600s
```

Below the per-call worst case (5 iterations × (30s + 30s) = 300s), and the timeout fires on
work that was going to succeed. Above the visibility timeout, and a *legitimately running*
task has its run reclaimed by another worker underneath it — two workers executing the same
run, which is the failure the timeout exists to avoid. Changing any one of the three without
the others re-checked breaks the chain.

### Tradeoffs
- A genuinely slow task is killed and retried, which costs tokens against a 20/day quota.
- **300s is reasoned, not measured.** It is derived from the configured worst case, and no
  task duration distribution has been recorded to confirm real tasks sit well beneath it.
- The bound is wall time, so a task waiting on a rate limit spends its budget waiting.

### Consequences
- `TIMED_OUT` becomes reachable, and the state machine stops documenting something impossible.
- The retry path is **shared with `FAILED`** — a timed-out task returns to `READY` like any
  other failure, so it cannot behave differently on its next attempt.
- Dependents of a permanently timed-out task are skipped by the existing `_skip_unreachable`
  fixpoint; no new propagation logic.

### Reconsider if
Measured task durations approach 300s — then the bound is shaping behaviour rather than
catching pathology, and it should be raised *together with* the visibility timeout. Also
reconsider if per-task claiming ever arrives, which would make option 3 viable for the first
time.

---

## ADR-010 — Tasks are persisted as they happen, and a reclaimed run resumes

**Date** 2026-09-13 · **Status** Accepted (V1.1)

### Context
V0.8 made a crashed worker's run recoverable: the visibility timeout returns it to `QUEUED` and
another worker claims it. What "recovered" meant was **re-executed from the beginning**.

That was tolerable only because of a property AMOS does not control for: every tool is read-only.
`docs/12-event-system.md` already records the exception — `remember_fact` writes, and a duplicate
store is harmless *by luck*, because supersession makes it a no-op.

### Problem
Resumption was impossible, for a reason that is not obvious from the outside: **nothing is
persisted until the run finishes.** `RunRepository.record_success` writes the tasks, steps,
LLM calls and tool calls in one batch at the end. A run killed mid-execution leaves a `runs` row
and nothing else — so there is no record of which tasks had already succeeded, and nothing to
resume *from*.

The second problem only appears once the first is solved. If a resumed run calls the planner
again, it gets **a different plan** — the planner is an LLM and is not deterministic. The stored
task ids would not correspond to the new plan's, and "skip the tasks that already succeeded"
would be meaningless.

### Options
1. **Re-execute from the start** (status quo). Correct only while every tool is read-only.
2. **Checkpoint task outcomes, re-plan on resume, match by description.** Fuzzy matching of
   model-generated text to decide what to skip.
3. **Persist the plan when it is made, and treat the stored task rows as the plan.** On resume,
   reconstruct the DAG from the rows, skip what succeeded, run the rest. No second planning call.
4. **Task-level idempotency keys.** Deduplicates a repeat execution; does not avoid it.

### Decision
**Option 3.** Tasks are written when the plan is validated, updated at every terminal transition,
and on reclaim the stored rows *are* the plan.

### Why
Option 2 decides control flow by comparing two pieces of model-generated prose. That is exactly
the boundary the project's golden rule puts on the other side — the LLM proposes, deterministic
code decides — and a near-match would silently skip the wrong task.

Option 4 is the thing that usually gets called "the fix for at-least-once", and it is weaker than
it sounds here: an idempotency key makes a *repeat* harmless, but the repeat still costs the full
run's tokens against a 20/day quota. Resuming avoids the work rather than tolerating it.

Option 3 also removes a planning call from every reclaim, which is not a side benefit at this
quota — it is a fifth of a day's budget.

**The plan is state.** That was ADR-002's argument for persisting before building the planner,
and this is the same argument one layer down: a plan that exists only in memory is a decision the
system cannot be held to.

### How the seam is kept
The executor has **no database access** and does not acquire any. It takes a `TaskCheckpoint`
protocol — one method, called on each terminal transition — exactly as it already takes a
`TaskRunner`. The persistence layer implements it; tests pass a fake, or nothing at all.

Checkpoint writes are **outside the run's transaction** and are allowed to fail: a checkpoint
that fails loses resumability for that task, and must never fail the run that is otherwise
succeeding. The same reasoning as episodic recording in `api/persistence.py`.

### Tradeoffs
- **One database write per task transition** instead of one batch at the end. At ≤10 tasks per
  plan this is not a throughput concern, and it would be at a hundred.
- A resumed run's `attempt_count` on the *run* advances while its completed tasks' do not, so
  "how many attempts did this take" now has two answers at two levels. Both are recorded.
- **A stored plan cannot be improved.** If the first attempt's plan was bad, resumption faithfully
  re-runs the bad plan. Re-planning on failure is a separate, unbuilt feature (`docs/17`), and
  this decision makes it a deliberate choice rather than an accident.
- Partial task state is now visible in `GET /v1/runs/{id}` **while a run is still executing**,
  which is a feature and also means a client can observe a task in `RUNNING`.

### Consequences
- `steps` being one row per run — listed as technical debt since V0.4 — is unchanged here.
  This ADR covers tasks, not steps.
- The V0.8 claim mechanism is untouched: claiming is still per run.
- Delivery is still **at-least-once**. Resumption narrows the window in which duplicate work
  happens; it does not close it. A worker that dies *between* finishing a task and checkpointing
  it will redo that task.

### Reconsider if
Plans grow large enough that per-transition writes matter, or re-planning on failure is built —
at which point "the stored rows are the plan" needs an explicit escape hatch rather than being
the only path.

---

## ADR-011 — Adversarial cases are tests; the eval suite gets a stored baseline

**Date** 2026-09-13 · **Status** Accepted (V1.2)

### Context
`docs/22-resume-evidence.md` has said since V1.0 what the evaluation suite does *not*
demonstrate: six goals, twelve retrieval questions and ten routing cases, **all self-authored**,
with no adversarial cases and no human calibration of the judge. `make eval` prints a scorecard
and discards it, so "did that change make things worse?" has never been answerable.

### Problem
Two problems that look like one.

1. **Nothing adversarial is tested.** The corpus is AMOS's own documentation, which is friendly
   by construction. Nothing checks what happens when retrieved text tries to instruct the model.
2. **No score is retained.** Each run is a number on a terminal, so regressions are invisible
   unless someone remembers the previous figure.

And a constraint that shapes both: **`make eval` costs real quota** — 20 requests/day on
`gemini-3.5-flash`. A suite big enough to characterise quality would be unrunnable, and a suite
that cannot be run is not a gate.

### Options
1. **Add adversarial cases to the golden goal set.** Uniform, and every one costs quota forever.
2. **Adversarial cases as deterministic tests**, with the eval suite unchanged.
3. **Split by what is being asserted**: boundary behaviour as tests, judgement behaviour as
   eval cases.
4. Do nothing and keep stating the gap.

### Decision
**Option 3**, plus a committed JSON baseline that `make eval` compares against.

The split follows the existing security principle rather than inventing a new one:

> Security is enforced in code that never reads model output.

If the assertion is **"the boundary holds even assuming the model is fully compromised"**, then
the model's cooperation is irrelevant and a `FakeProvider` that *complies with the attack* is a
stronger test than a real model that might happen to resist. Those are unit tests: free,
deterministic, and they run in CI on every push.

Only assertions that genuinely need a model's judgement — does it refuse when the corpus is
misleading rather than confidently repeating it — belong in the quota-costing suite.

### Why
Option 1 puts deterministic assertions behind a rate limit, which makes CI unable to run the most
important security checks in the project. It also scores them with an LLM judge, when the
property being checked is decidable by code.

Option 2 leaves the quota-costing suite exactly as unrepresentative as before.

The baseline is a **committed JSON file**, not a table. One machine, and the point is that a
score change shows up in a diff and in git history next to the commit that caused it. A table
would put the history somewhere `git log` cannot see it.

### Tradeoffs
- **The split has to be judged case by case**, and the boundary is not always obvious. The test
  is: *could code decide this?* If yes, it is a test.
- A committed baseline is a file people will be tempted to update to make a failure go away.
  Mitigated only by review, and by the file recording *when and with what* each number was
  measured.
- Baselines are **per-corpus and per-model**. Changing either invalidates them, and the file has
  to say so or the comparison silently becomes meaningless.

### Consequences
- CI gains real adversarial coverage at zero quota cost, and it runs on every push rather than
  when someone remembers.
- `make eval` gains a regression gate but stays a deliberate, human-run command.
- **This does not fix the independence problem.** Cases written by the person who built the
  system are still not independently authored, and enlarging the set does not change that.
  `docs/16-evaluation.md` keeps saying so.

### Reconsider if
A second person starts authoring cases — at which point the golden set can grow past what one
quota can run, and the interesting question becomes sampling rather than coverage.

---

## ADR-012 — Delegation is a tool, and its bound is structural

**Date** 2026-09-13 · **Status** Accepted (V1.3)

### Context
`agents/messages.py` has defined `AgentTask` — the structured agent-to-agent contract the project
brief's §10 asks for — since V0.7, and **nothing has ever constructed one**. It has been listed as
technical debt in `engineering/current-state.md` ever since. The orchestrator assigns every task;
agents never talk to each other.

The concrete gap: a Researcher that retrieves "the quota is 20 requests per day" and is then asked
for 15% of it **cannot do the arithmetic**. `calculator` is not in its allowlist, deliberately —
disjoint allowlists are what make routing meaningful. Its only options are to answer from its head
(the exact failure tools exist to prevent) or to fail.

### Problem
How does one agent hand work to another, without inventing a second mechanism for something the
system already does well?

### Options
1. **A `delegate` tool** the specialist calls like any other.
2. **An extra LLM turn per task** — "do you need another agent?" — before or after execution.
3. **Router-level decomposition**: detect multi-capability goals up front and split them.
4. **Free-form handoff**: one agent writes a message, another reads it.

### Decision
**Option 1.** Delegation is a tool named `delegate`, taking a target agent, an instruction and
context; it constructs an `AgentTask`, runs the target specialist, and returns its answer as tool
output.

### Why
The same argument that made retrieval a tool at V0.5. A tool already gets, for free and without
a second code path: **schema-validated arguments** before anything executes, a timeout, an entry
in the trace and in `tool_calls`, an outcome the model can see and react to, and — most
importantly — **per-agent allowlisting**. Which agents may delegate is then the same mechanism as
which agents may search, and needs no new concept.

Option 2 spends a call per task asking a question whose answer is usually no. On a 20/day quota
that is the difference between six goals and three.

Option 3 is not delegation; it is planning, and the planner already exists. It also cannot help
mid-task, when the need for another capability is discovered rather than predicted.

Option 4 is what §10 of the brief explicitly warns against, and the reason `AgentTask` exists.

### The bounds, and why they are structural
An agent that can delegate can delegate to an agent that can delegate. Three bounds, none of them
a prompt instruction:

| Bound | Mechanism |
|---|---|
| **Depth** | A delegate is built with a registry that **does not contain `delegate`** once the cap is reached. Not a check it could argue past — the tool does not exist. |
| **Budget** | A per-run ceiling on total delegations, enforced by the tool's own counter. |
| **No self-delegation** | Rejected as an invalid argument, before execution. |

Cycles need no separate detection: researcher → analyst → researcher terminates because depth is
bounded, and depth is bounded by removing the capability rather than refusing the call.

The delegate runs with **its own** allowlist, never the caller's. Delegation moves work, not
authority — otherwise it would be a privilege-escalation path dressed as a feature, and a prompt
injection that talked a Researcher into delegating would inherit whatever the Analyst can do.

### Tradeoffs
- **Cost amplification is real.** Each hop is at least one LLM call, and the caller then continues
  its own loop with the result. A depth of 2 with a budget of 3 can triple a task's cost, which is
  why the budget is small and configurable rather than generous.
- **The orchestrator still exists**, so there are now two ways work gets distributed: planned
  decomposition (up front, deterministic) and delegation (mid-task, model-decided). That is a
  genuine increase in surface area, justified only because they answer different questions —
  *what are the steps* versus *I have hit something I cannot do*.
- A delegated failure surfaces as a tool failure to the caller, which means the caller's model
  decides what to do about it. That is the same trust boundary every other tool has.

### Consequences
- `AgentTask` finally has a caller, and §10 is true rather than aspirational.
- The Researcher's allowlist stays free of `calculator`. Specialisation is preserved *because*
  delegation exists — the alternative pressure was to widen allowlists until they overlapped, at
  which point routing accuracy would stop meaning anything.

### Reconsider if
Delegation depth ever needs to exceed 2, which would suggest the planner should have decomposed
the goal instead — or if a `WRITE` tool is ever added, at which point "the delegate uses its own
allowlist" needs re-examining against a caller that could be induced to delegate.

---

## ADR-013 — Authentication, and isolation enforced where queries are built

**Date** 2026-09-13 · **Status** Accepted (V1.4)

### Context
`docs/13-security.md` has carried a table of controls marked ❌ since V0.2 — authentication,
authorization, data isolation — each with the same justification: *single local user*. Every run,
memory and document is globally readable, and the document says plainly that **AMOS is not safe to
expose publicly**.

This ADR is also a **reversal**. `docs/01-requirements.md` lists multi-tenancy under *explicit
non-goals*: "not deferred-and-planned; out of scope, and pretending otherwise would distort the
architecture." That was the right call for V0.1–V1.3 and it is being overturned deliberately
rather than quietly — the requirement document changes with this decision, and says it changed.

### Problem
Two problems, and the second is the one that actually matters.

1. Anyone who can reach the API can do anything. There is no notion of *who*.
2. **Isolation is a property that fails silently.** A missing `WHERE user_id = ...` does not raise,
   does not log, and returns *more* data rather than less — so the failure looks like a working
   feature. One forgotten filter in one query is a data breach, and it is invisible in review
   because the code looks like every other query.

### Options for the credential
1. **Hashed API keys** in a `users` table, presented as `Authorization: Bearer <key>`.
2. **JWT** with a signing key.
3. **OAuth / an external identity provider.**

**Decision: option 1.** JWT buys stateless verification, which is worth having when auth is
checked by many services that should not share a database — AMOS is one process with one database
(ADR-004), so it buys nothing and costs key management and rotation. OAuth solves identity
federation, which is not a problem anyone has here. Keys are stored as **SHA-256 hashes**: the
database should not contain anything that grants access if it leaks.

### Options for enforcement
1. **Filter in each handler.**
2. **Pass `user_id` into every repository method.**
3. **Construct the repository with its owner**, so no query can be built without one.
4. Postgres row-level security.

**Decision: option 3.** `RunRepository(session, actor)` takes the acting user at construction, and
every query it builds applies the filter from that field. There is no method that can be called
without an owner, because there is no repository without one.

Option 1 puts the guarantee in the layer most likely to be copy-pasted. Option 2 is one forgotten
argument away from a leak, and the forgotten version still compiles and still returns rows.
Option 4 is genuinely stronger and is the right answer at a different scale; it moves the
guarantee into the database at the cost of every query running under a session variable that must
be set correctly on a pooled connection — a new failure mode, in exchange for defence against a
class of mistake option 3 already makes hard. **Reconsider if** a second service ever shares this
database.

Backing that up, a test asserts no raw `select(Run)`/`select(Memory)` appears in the repository
outside the scoped helper — so the mechanism cannot be bypassed by writing a query the ordinary
way.

### The corpus is shared, and that is deliberate
`documents.user_id` is **nullable**, and `NULL` means *system corpus*: readable by everyone,
writable by no one through the API. AMOS's own documentation is the corpus, and giving each user a
private copy would mean re-embedding it per user — roughly ten minutes of quota each — to isolate
data that is already public in the repository.

Retrieval therefore matches `user_id IS NULL OR user_id = :actor`. That is the one place isolation
is deliberately not total, and it is a decision rather than an oversight.

`runs` and `memories` are **not** nullable. A run is what someone asked and what came back; a
memory is a fact about a person. Both are private by construction.

### Tradeoffs
- **Every table and query changes.** This is why it went last: doing it once against a schema that
  has stopped moving is far cheaper than twice.
- The backfill assigns every existing row to one owner. That owner is created by the migration,
  and is the only account that exists until someone makes another.
- **A leaked key is full access for that user** until it is rotated, and there is no rotation
  endpoint. Stated in `13-security.md` rather than implied to be solved.
- Auth is per *user*, not per *scope*. There are no read-only keys and no permissions within an
  account — that is authorization, and it is explicitly still ❌.

### Consequences
- `docs/01-requirements.md`'s non-goal list loses multi-tenancy and says when and why.
- `docs/13-security.md` flips three rows from ❌ to ✅ and gains the new residual risks.
- `docs/02-system-architecture.md`'s "API Layer — … auth" becomes true for the first time.
- **"AMOS is not safe to expose publicly" is still true.** There is no TLS, no rate limiting, no
  audit of authentication attempts, and no key rotation. Authentication is a precondition for
  exposure, not a sufficient one, and the documents keep saying so.

### Reconsider if
A second service shares the database (row-level security), or accounts need permissions within
them rather than only identity (authorization, a separate milestone).

---

## ADR-014 — Transient provider errors are retried once, inside the call's deadline; a second provider is deferred

**Date** 2026-11-20 · **Status** Accepted (V1.6)

### Context
On 2026-11-19 two of nine eval goals failed on Gemini `503 UNAVAILABLE — high demand`. Both were
raised by calls **outside** the task executor — the planner and the synthesis step — where nothing
retries, so the whole run failed and the work of finished tasks was lost with it. Inside a task the
same error *was* retried, but by re-running the entire task: routing, every tool call, the critic.

The error could not even be named. `GeminiProvider` mapped every `ServerError` to the generic
`ProviderError` with no status, so no caller could tell a 503 from a malformed request, and the API
answered it as HTTP 500.

The roadmap's V1.6 was a second provider (Groq), justified by `gemini-3.5-flash`'s 20 requests a
day. The account page read on 2026-11-19 removed that reason: the default model,
`gemini-3.5-flash-lite`, allows **500 a day**. Groq's `openai/gpt-oss-120b` allows 30 requests a
minute but only **8K tokens a minute** — about one AMOS goal a minute.

### Problem
Make a transient provider failure cost one extra call, wherever it happens, without weakening any
bound that already exists — and decide whether a second provider is still worth building.

### Options
1. **Retry in each caller** (planner, router, agent, critic, synthesis).
2. **A wrapper over `LLMProvider`** that retries a typed transient error, applied where providers
   are built.
3. **Rely on the executor's task retry** and wrap the planner and synthesis in tasks.
4. **A second provider as a fallback** on 503.

### Decision
**Option 2.** `ProviderUnavailableError(ProviderError)` names a 5xx. `RetryingProvider` wraps any
provider and retries that error **once**, after a jittered delay (`backoff_delay`, the same
function the executor uses), and **never** retries anything else: not a 429 (the quota is still
spent), not an auth error, not a 4xx, not a timeout. `build_provider` applies it and becomes the
only place a provider is constructed, so the judge and the routing CLI get it too.

**The retry shares the caller's deadline.** The second attempt receives whatever remains of the
original `timeout`, and no retry happens when too little remains. ADR-009's chain —
*per-call bound < task timeout (300 s) < visibility timeout (600 s)* — holds unchanged: a retried
call still cannot outlive the bound its caller set. Giving the retry a fresh timeout would have
doubled the per-call worst case and let a task breach 300 s on work that was going to succeed.

**The executor stops retrying what cannot succeed.** A daily `ProviderRateLimitError` (it resets
at midnight Pacific) and a `ProviderAuthError` now fail the task permanently at once, with the
reason. Before, both were retried up to three times with backoff.

**Groq is deferred, not built.** The 503 problem is solved by option 2, the daily ceiling is 25×
larger than the roadmap assumed, and Groq's token-per-minute limit makes it a slower provider, not
a bigger one. `GROQ_API_KEY` stays in `.env` for the day it is needed.

### Why
Option 1 is five copies of a policy that must agree, and the next caller written forgets. Option 3
fixes the two callers it names and leaves the cost of a 503 inside a task — a whole re-run — where
it was. Option 4 solves a problem with a second dependency, a second contract and a second quota,
when a single retry clears the observed failure; it also changes the model mid-run, which makes the
eval's numbers a mixture of two series.

### Tradeoffs
- **Retries can stack.** Inside a task, a persistent 503 costs up to 2 calls per task attempt —
  6 across the executor's three. That is bounded, and a single 503 now costs one call instead of a
  whole task.
- One retry is a guess that "high demand" passes in about a second. It is not measured; the
  jittered delay is under a second and the deadline caps everything else.
- A daily 429 now fails a run sooner. That is the point: three retries an hour apart would not
  have succeeded either, and each one spent a call.

### Consequences
- `ProviderUnavailableError` maps to HTTP **503** in the API's one status table.
- The embedding client classifies a 5xx the same way.
- A provider is built once per process at startup; the memory reconciler shares it rather than
  building a second one.

### Reconsider if
503s outlast one retry in practice (then a second, longer attempt or a fallback provider); or
flash-lite fails the complex goals of V1.10 on quality (then Groq or `gemini-3.5-flash` as the
higher-quality model for the writer); or the free-tier limits change.

---

## ADR-015 — A run is recorded as it happens, attributed to its task, agent and purpose

**Date** 2026-11-20 · **Status** Accepted (V1.7)

### Context
`GET /v1/runs/{id}` was meant to answer "what exactly happened". A survey for the demo page found
it could not:
- Model and tool calls were **batch-inserted when the run finished**, in one transaction, so they
  shared one `created_at` and their order was arbitrary. A run in flight showed none of them.
- No call said which task, which agent or what for: the planner, the router, a specialist, the
  critic and synthesis were indistinguishable rows.
- Routing decisions and critic verdicts were **never stored**; with planning on, the orchestrator's
  result dropped even the in-memory ones.
- A delegate's own calls and tokens were **discarded**, so `total_tokens` under-counted every
  delegated run; failed task attempts and failed runs stored no calls at all.
- Task rows recorded only terminal states, so a running or retrying task looked `PENDING`.

### Problem
Make the stored record complete enough to draw the run — its graph, its lanes, every call in order,
every decision — while it runs and after, without changing what the agents do.

### Options
1. **Keep the batch, add columns.** Attribution, but still nothing until the end.
2. **One append-only event table for everything**, calls included, as JSON.
3. **Write the typed call rows as each call finishes**, plus a small event table for what has no
   table, ordered by one shared sequence.

### Decision
**Option 3.**
- A **call context** — task, task attempt, run attempt, agent, purpose — lives in a contextvar
  beside the run id, changed only through a `call_scope(...)` that sets *and resets*.
  `asyncio.wait_for` runs its coroutine in the same task on Python 3.14 and does not copy context,
  so an un-reset `.set()` would leak into the caller and mislabel every later call.
- `RecordingProvider` sits **inside** `RetryingProvider`: every real attempt — a failed 503, a 429
  with its scope — is one `llm_calls` row, written in its own short transaction as the call returns.
- Tool calls are recorded at `ToolUsingAgent._invoke`, the one point every tool outcome passes
  through, NOT_FOUND included — which also covers delegates, revisions and the reconciler.
- `run_events` holds only what has no table: plan created, task started / retrying / timed out /
  finished, routed (agent and reason), critic verdict, delegation started / finished.
- One Postgres sequence is the `DEFAULT` for a `seq` column on all three tables: a total order
  across them, and a stable key for a client to diff on.

### Why
Option 1 leaves the page blind until the run ends — the opposite of "watch it live". Option 2
throws away typed rows that the trace endpoint, its schemas and the eval's counts depend on, or
duplicates them. Option 3 keeps every existing reader working and adds one writer per seam.

The precedent is V1.1's `RunProgress`: short transactions around SQL only. **No model call ever runs
inside an open session**, so a write per call never nests a pool checkout.

### Tradeoffs
- **More writes**: one small transaction per call. A run makes tens of calls; the cost is noise
  next to a model round-trip, and it is measured by the existing latency figures, not assumed.
- **A sequence is assigned at insert, not commit**, so concurrent tasks can commit out of order.
  Clients therefore poll the **whole run** (a few hundred rows) rather than "everything after N" —
  a cursor would skip a row that committed late.
- `total_tokens` is now the **sum of stored calls**: it rises for delegated and retried runs,
  because they were under-counted before. Not one series with earlier runs' figures.
- No write is attempted for a call cancelled mid-flight (a task timeout); the timeout itself is an
  event.

### Consequences
- The batch insert at the end of a run stays for agents that do not record live (tests' fake agents
  and direct agents) and is skipped when live rows exist — not a global switch.
- Failed runs keep their calls. Call metrics move into the recorder, so the worker emits them too.
- Retrieval passages carry their chunk id, so a citation can open its source.

### Reconsider if
Runs grow to thousands of calls (then a cursor with commit-ordered ids, or server-sent events), or a
second consumer needs the stream (then the event table becomes the interface, not an implementation
detail).

---

## ADR-016 — Features are switchable per run, off only

**Date** 2026-11-20 · **Status** Accepted (V1.7)

### Context
Planning, multi-agent, the critic, delegation and memory are environment flags read once at
startup. The demo page is meant to show what each one does — the same goal with and without the
critic — and restarting the server between runs is not a demo.

### Decision
`POST /v1/goals` and `/v1/goals/async` accept `options` — `planning`, `multi_agent`, `critic`,
`delegation`, `memory`, each `true`, `false` or absent. **A run may switch off what the server
enabled, never switch on what it disabled** (422): a server flag is an operator's cost or capability
decision. Options are stored on the run (`runs.options`), so the worker applies them, the trace
shows them, and an idempotent replay cannot change them.

A run with options gets its own agent, built from the **shared** provider and tool registry —
constructors do no I/O, and the worker already builds an agent per run. `memory: false` also skips
episodic recording and the memory reconciler, which would otherwise write memory anyway.

### Why
Off-only keeps the server's configuration the ceiling. Allowing a request to turn on, say,
delegation on a server that disabled it would make the environment file advisory.

### Tradeoffs
- Per-run construction is a small cost on every run that sends options.
- Bounds (iterations, attempts, timeouts) are **not** per-run options: they are safety limits, and a
  request lowering one is harmless but raising one is not — so none are exposed.

### Reconsider if
Options need to be per *user* rather than per run (that is authorization, not configuration).

---

## ADR-017 — The demo page is plain HTML, CSS and JavaScript, served by the API, with nothing from outside

**Date** 2026-11-21 · **Status** Accepted (V1.8)

### Context
V1.7 made the whole run readable over HTTP. The demo needs a page that shows it live on a
projector to someone who has never seen AMOS: the plan as a graph, which agent did what, every
call and decision, the answer with its sources, and the system's health and quotas around it.
Anshul does not write the frontend and has to explain it.

### Options
1. **A framework and a build step** (React/Vite or similar).
2. **Server-rendered templates** (Jinja + HTMX).
3. **Plain HTML, CSS and ES modules**, served as static files by the existing app.

### Decision
**Option 3.** `src/amos/web/` holds `index.html`, one stylesheet and a handful of ES modules,
served by Starlette's `StaticFiles` — already a dependency, no `aiofiles` needed. `GET /` returns
the page; everything it shows comes from the V1.7 read API with the user's API key.

- **Nothing from outside.** The typeface (Inter, OFL) is a file in the repository; there is no
  CDN, no web font service, no analytics. A strict Content-Security-Policy —
  `default-src 'self'` — makes that enforced rather than intended, and a test fails if a static
  file names an external URL.
- **Model output is untrusted (N-12).** It is rendered with `textContent`, and answers go through a
  small markdown renderer that builds DOM nodes; `innerHTML` is never given text that came from a
  model, a document or a user.
- **The key** lives in `sessionStorage` unless the presenter chooses "remember on this device".
- **Browser tests** use Playwright for Python, a *development* dependency, driving the Chrome
  already installed — no browser download. Opt-in (`make browser-test`), like the live tests.

### Why
A build step is a second toolchain to install, upgrade and explain, for a page with one author and
one audience. Templates would put rendering on the server and make every live update a round trip
of HTML; the page's main job — redrawing a run several times a second — is client-side work.
Plain modules keep the whole frontend readable in a browser's view-source.

### Tradeoffs
- **No component library**, so every control is written by hand: more code than a framework would
  need, all of it inspectable.
- **Polling, not streaming.** The page re-reads the whole run about once a second (ADR-015 explains
  why not a cursor). Fine for one presenter; not a design for many viewers.
- ES modules need a modern browser; the target is one known machine.

### Consequences
- The API serves a page, so `/` is no longer a 404. It needs no key; the data behind it does.
- Hatchling packages `src/amos/web/` with the wheel, so the page ships with the service.

### Reconsider if
The page grows past one presenter (then server-sent events, ADR-015's reconsider-if), or a second
person maintains the frontend (then a framework's conventions start paying for themselves).

---

## ADR-018 — Web research through self-hosted SearXNG, and pages only from this run's own results

**Date** 2026-11-21 · **Status** Accepted (V1.9)

### Context
Every goal AMOS answers today is about its own documentation. The demo needs goals about the
world — costs, comparisons — answered with web citations, **without giving a model an open
network**: `http_get` is allowlisted to seven documentation hosts for exactly that reason.

### Options for search
1. **A keyed search API** (Tavily, Brave, …).
2. **Self-hosted SearXNG**, a metasearch engine run as a container beside Postgres.
3. **The model provider's own search grounding.** Gemini 3's free tier allows **0** grounded
   requests (account page, 2026-11-19).
4. **Scraping a search engine's HTML** — no.

### Decision — search
**Option 2.** Anshul has no keyed provider, and SearXNG needs no key and no account. It runs from
`compose.yaml`, **pinned** to `searxng/searxng:2026.11.6-ac11e14c5` (Docker Hub, 2026-11-21 — two
weeks old, per the project's rule), bound to `127.0.0.1:8888`, configured with
`use_default_settings: true` plus three changes: JSON output enabled (`search.formats`, without
which the API answers 403), the limiter off (a private instance), and the secret from
`$SEARXNG_SECRET`. All read from docs.searxng.org on the day. `web_search(query)` calls
`GET /search?q=…&format=json` and returns title, URL, snippet and engine for the top results. The
tool's interface names no provider, so a keyed one can sit behind it later.

### Decision — fetching pages
`fetch_page(url)` fetches a page **only if `web_search` returned that exact URL earlier in the same
run** — a provenance allowlist enforced in code. Provenance is read from the **stored run record**
(ADR-015): the run's own `web_search` rows. Nothing in memory to leak between runs or lose on a
restart, and without a database the tool refuses everything: it fails closed.
On top of provenance, the existing guard from `http_get`, now shared: `https` only, the resolved
address must not be private, loopback or link-local. Redirects are followed only within the same
host, each hop re-checked. Responses are capped at 500 KB and reduced to text with the standard
library's `html.parser` (no new dependency), 20 000 characters at most.

**Page text is untrusted data (N-12).** The tool's output says so, the researcher's instruction
says to treat it as material rather than instructions, and a fixture page containing a prompt
injection is part of the tests.

### Why
A keyed API is the better search but the wrong dependency for this project today. Provenance turns
"the model may fetch any URL" into "the model may read what search found", which is the whole
research workflow and nothing more. Reading provenance from the stored record reuses V1.7 instead
of adding a second, in-memory source of truth.

### Tradeoffs
- **SearXNG's quality varies** with the upstream engines it queries, and they can rate-limit it.
  Measured on ten fixed queries (`experiments-log.md`), not assumed.
- **A ~100 MB container** in the demo's dependencies; `make demo` (V2.0) checks it is up.
- Search results and pages change daily, so tests use **recorded fixtures**; one live test is opt-in.
- Following no redirect to another host means some links fail. Acceptable: a refusal the model can
  read is better than a hop nothing checked.

### Consequences
- `AMOS_WEB_ENABLED` (default **off**) adds both tools to the researcher; `web` is a per-run option.
- `AMOS_SEARXNG_URL` names the instance (default `http://127.0.0.1:8888`); `/v1/system` reports
  whether it answers.

### Reconsider if
SearXNG's measured relevance is too low for the demo questions (then a keyed provider behind the
same tool), or a page needs JavaScript to render (then this is the wrong tool, not a reason to
add a browser).

---

## ADR-019 — A per-run budget, enforced in code

**Date** 2026-11-21 · **Status** Accepted (V1.9)

### Context
Web research fans out: search, read three pages, search again. Every step is a model call or a tool
call against a finite quota (500 model calls a day). The existing bounds are per call, per task and
per run *attempt*; nothing bounds what one run may spend in total.

### Decision
`RunBudget` — model calls, tool calls and page fetches — set per run by `RunService` and the
worker (`AMOS_RUN_MAX_LLM_CALLS=40`, `AMOS_RUN_MAX_TOOL_CALLS=40`, `AMOS_RUN_MAX_PAGE_FETCHES=8`),
and checked at the seams ADR-015 already built:
- **Model calls** in `RecordingProvider`: past the budget, `BudgetExhaustedError`, which the
  executor does not retry. The synthesis call is exempt, so a run that spent its budget can still
  combine what it found.
- **Tool calls** in `ToolUsingAgent._invoke`: past the budget, the call is **denied** with a reason
  the model can read, so it answers with what it has instead of crashing.
- **Page fetches** in `fetch_page`.

Each limit, when first reached, is recorded as a `budget.exhausted` event, so the page shows it.

### Why
A budget is a guarantee, so it belongs to code (rule 1 of the working agreement); a prompt asking
the model to be frugal is not one. Enforcing it at the seams that already see every call means one
check each, not one per caller.

### Tradeoffs
- The limits are **guesses** sized from V1.7's live runs (10–12 calls for a two-task goal) with room
  for research; complex goals (V1.10) will test them.
- Exhausting the model budget inside a task fails that task permanently; the run ends partial or
  failed, with the reason in its record.

### Reconsider if
Demo goals hit the budget while still making progress (raise it), or a run legitimately needs
different budgets per agent.

---

## ADR-020 — A writer with no tools composes deliverables, and code checks what it cites

**Date** 2026-11-21 · **Status** Accepted (V1.10)

### Context
"Plan a 3-day trip under a budget" or "compare X and Y and recommend one" need a *deliverable* — an
itinerary, a comparison table — assembled from facts several tasks found. Today the orchestrator's
synthesis call merges task answers into one paragraph, and nothing distinguishes a fact a source
gave from a suggestion the model made up. For a plan, both are useful; presented alike, the second
is laundered into the first.

### Decision
- **A `writer` agent with no tools.** It composes the deliverable from the material it is given —
  the results of the tasks it depends on — and cannot search, compute or recall. It is not a
  delegation target and cannot delegate: it writes; it does not commission.
- **Two sections, by rule.** Every fact cites its source inline as `(source: <url or file>)`.
  Suggestions with no source go under a heading **Not sourced**. General knowledge is allowed —
  "carry cash for small vendors" — but only where it is labelled as such.
- **Code checks the citations.** After the writer answers, every `(source: …)` and every URL it cites
  is compared with what this run actually saw — search results and pages read, retrieval citations —
  read from the stored run record (ADR-015). A citation the run never saw is a caveat and drops the
  confidence to low. A model is not asked whether the citations are real; code looks.
- **The critic judges the writer against the material it was given**, not against the writer's own
  tool calls (it has none), and does not flag what is under *Not sourced*.
- **The planner learns the shape**: research tasks, then arithmetic, then one writing task that
  depends on them. **When the run's final task is the writer's, its deliverable is the answer** —
  the synthesis call would only re-summarise a structured document into a paragraph.

### Why
A tool-less writer cannot introduce a fact by fetching it, so every fact in the deliverable traces to
an earlier task's record. Checking citations in code is the V0.1 rule — LLMs handle uncertainty,
software handles guarantees — applied to the one claim a model is most tempted to fabricate: where
something came from. The *Not sourced* section keeps the helpful part of a plan without pretending it
was researched.

### Tradeoffs
- The citation check proves a cited source was **seen**, not that it **says** what is claimed. That
  second judgement stays with the critic, and is weaker for it.
- One more agent to route to; the router's labelled set grows to keep measuring it.
- A plan whose facts are thin will be mostly *Not sourced* — visible, by design.

### Reconsider if
The writer needs a fact mid-composition often enough that a research round-trip becomes the norm
(then let it delegate to the researcher), or the citation check produces false alarms on legitimate
sources (then the source ledger is missing a tool).

---

## ADR-021 — A fallback chain of models, and a stronger model for the writer

**Date** 2026-11-22 · **Status** Accepted (V1.11) · **Revisits** ADR-014's deferral of a second provider

### Context
ADR-014 deferred a second provider: one retry cleared most of Gemini's 503s, and flash-lite's 500
calls a day were enough. Both assumptions broke in the demo's own workloads. A live complex goal
lost a task to 503s that outlasted the retry, and a probe on 2026-11-22 found **every** Gemini model
overloaded at once — flash, both Gemma 4 models, and flash-lite. ADR-014's *reconsider if* named both
triggers: "503s outlast one retry in practice", and "flash-lite fails the complex goals on quality".
The Goa plan, even when it completed, was generic; part of the cause is the smallest model writing it.

### Options
1. **More keys or accounts on Gemini's free tier.** Refused: Google's API terms forbid circumventing
   documented limits, and keys in one project share its quota anyway.
2. **A second Gemini model** as a fallback. Its own quota (Gemma 4: 14,400 a day), no new code — but
   the same infrastructure, so it does not help when Google is overloaded.
3. **A second provider, Groq**, on separate infrastructure, over its OpenAI-compatible API with
   `httpx` (already a dependency) — no SDK.
4. **Both**, as an ordered chain.

### Decision
**Option 4.** `FallbackProvider` tries, in order: the Gemini primary (`flash-lite`), a Gemini
fallback model (`gemma-4-26b-a4b-it`, measured to answer structured output), then Groq
(`openai/gpt-oss-120b`). A call moves to the next member on a 503, a rate limit or a timeout with time
still left in the caller's deadline — **shared across the chain**, so ADR-009's bounds still hold. The
whole chain sits inside ADR-014's single retry; each member has its own rate limiter and is recorded
on its own row, so the page shows which model answered.

**One rule the chain must keep:** a conversation whose tool calls came from one model cannot continue
on a *different Gemini* model — Gemini 3 rejects function calls in its history that carry no
signature it made. Groq accepts any history. So once a tool loop has a tool call from model M, Gemini
members other than M are skipped for the rest of that loop; Groq can always take over. Checked in
code on every call, from the model recorded on each turn.

**The writer prefers Groq.** It has no tools, so no signature question arises, and `gpt-oss-120b` is a
far stronger writer than flash-lite. If Groq fails, the writer falls back down the same chain.

Groq specifics, read from its documentation on the day: `openai/gpt-oss-120b` allows 30 requests and
8,000 tokens a minute, 1,000 requests and 200,000 tokens a day; it supports tool use but not parallel
tool calls; **structured output cannot be combined with tools**, so with tools the schema is stated in
the instruction and the answer parsed, and without tools `json_schema` is used.

### Tradeoffs
- **Answers can come from different models within one run.** The record says which, per call.
- Groq's 8,000 tokens a minute make it slow for large prompts; it is a fallback and a writer, not
  the workhorse.
- A second provider is a second contract to keep working; both pass the same test suite on fixtures.
- Gemma's tool calling is unmeasured (it returned 503 when probed); it is used where it was measured.

### Reconsider if
Groq's free tier changes, Gemini's capacity problems stop, or the writer's output is not measurably
better on the complex goals than flash-lite's.
