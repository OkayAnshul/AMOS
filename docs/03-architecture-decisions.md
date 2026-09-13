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
