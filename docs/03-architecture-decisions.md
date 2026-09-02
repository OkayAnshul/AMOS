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

## ADR-007 — Seven documents written, seventeen stubbed

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
