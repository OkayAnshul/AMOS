# 23 — Glossary

**The engineering vocabulary**, not the domain one. Goal, Run, Task, Step, Agent, Tool and
Memory are defined in [`04-domain-model.md`](04-domain-model.md) and are deliberately *not*
repeated here — a second copy is the thing that goes stale.

What this covers is the other half: the terms a skeptical interviewer probes, each with what it
means, and **where in AMOS it actually appears**. A term with no file next to it is not in this
project. Reading for each is in [`24-study-plan.md`](24-study-plan.md), which carries the
verified links; this page is the index, not the syllabus.

> This file was a one-line stub reading "Scheduled for V0.4" until 2026-09-13 — seven milestones
> after V0.4. Its argument (do not duplicate the domain model) was right; the conclusion (write
> nothing) was not, because the vocabulary that needed pinning was never the entity names.

---

## Reliability and execution

**At-least-once delivery** — a queued job runs one *or more* times. AMOS's guarantee. A worker
can finish and die before recording that it finished, after which the visibility timeout makes
the run claimable again. `worker/queue.py`

**Exactly-once delivery** — running exactly once, guaranteed. **Not available**, and not claimed
anywhere in this repo. The correct response is idempotent work, not a stronger promise.

**Idempotency** — doing a thing twice has the same effect as doing it once. The property that
makes a retry safe. AMOS has it at *submission* (`idempotency-key` header dedupes a resubmitted
goal) and **not** at task level, which is the open gap. `api/persistence.py`

**Idempotency key** — a client-supplied identifier for "this is the same request I sent before",
so a retry after a timeout returns the original run instead of starting a second one.

**`SKIP LOCKED`** — a Postgres row-locking clause that steps over rows another transaction has
locked instead of blocking on them. Without it, N workers running the same claim query serialise
into one. `worker/queue.py:82`

**Visibility timeout** — how long a claimed job may stay claimed before it is presumed
abandoned and returned to the queue. Liveness is *inferred* from `claimed_at`, so nothing needs
to detect that a worker died. A slow-but-alive worker can have its run stolen; that is the
tradeoff. `worker/queue.py:132`

**Poison message** — a job that crashes every worker that touches it, and so is reclaimed
forever, starving the queue. Bounded by `attempt_count < max_attempts` in the claim query.
`worker/queue.py:183`

**Dead-letter queue** — where permanently failed jobs go to be looked at. **Not built.** A
given-up run is marked FAILED and nothing collects it.

**Exponential backoff** — waiting longer after each successive failure, so a struggling
dependency is not hammered. `orchestration/retry.py:28`

**Jitter** — randomising the backoff delay. Without it, clients that failed together retry
together and stampede in synchronised waves. AMOS uses full jitter.

**Circuit breaker** — stopping calls to a failing dependency entirely for a while. **Not
built**; retry budgets bound the damage more slowly.

**State machine** — a set of states plus an explicit table of which transitions are legal.
Illegal ones *raise*, they do not warn. The LLM cannot move a task between states — this is
deterministic code, per the golden rule. `orchestration/state.py`

**Terminal state** — one with no legal transition out. `SUCCEEDED`, `PERMANENTLY_FAILED`,
`SKIPPED`. The executor asserts every task reaches one.

**DAG** (directed acyclic graph) — the shape of a plan: tasks with dependencies and no cycles.
Validated before a row is written. `orchestration/plan.py`

**Topological order** — an ordering of a DAG where every task appears after everything it
depends on. `orchestration/plan.py:79`

**Partial success** — a real outcome, not a failure to classify. Three of four tasks succeeding
produced value; `PARTIALLY_COMPLETED` says so. `orchestration/executor.py`

---

## Retrieval

**Embedding** — text as a vector, positioned so that similar meanings are near each other.

**Cosine distance / similarity** — the angle between two vectors. Similarity = 1 − distance.
Only meaningful on **normalised** vectors. `rag/store.py`

**L2 normalisation** — scaling a vector to unit length. Skipping it after truncation makes
cosine ranking silently wrong: wrong order, no error. `rag/embeddings.py`

**MRL truncation** (Matryoshka Representation Learning) — keeping only the leading dimensions of
an embedding trained so that those carry most of the signal. AMOS truncates 3072 → 1536 because
pgvector's HNSW index supports at most 2000. **Re-normalisation afterwards is mandatory.**
ADR-008

**HNSW** (Hierarchical Navigable Small World) — an approximate-nearest-neighbour index. Fast and
*approximate*: it can miss a true neighbour, which is the accuracy-for-speed trade. Built after
the corpus is loaded, not before.

**ANN** (approximate nearest neighbour) — finding *probably* the closest vectors, much faster
than checking every one.

**Chunking** — splitting a document into retrievable pieces. Too small loses context, too large
dilutes relevance. AMOS is heading-aware with overlap. `rag/chunking.py:49`

**Overlap** — repeating text across chunk boundaries so a fact split by a boundary still appears
whole in at least one chunk.

**Grounding** — answering from retrieved text rather than model recall.

**Groundedness** — the measured degree to which an answer is supported by what was retrieved.
AMOS's one LLM-judged metric. `evaluation/judge.py:74`

**Refusal** — declining to answer when retrieval returned nothing relevant. The correct
behaviour, and scored as a success. Fabricating instead is the failure.

**Citation** — a chunk id attached to a claim, so a reader can check it.

**recall@k** — of the chunks that *should* have been retrieved, the fraction that appear in the
top k. **Strict recall@k** requires *all* of them. `rag/evaluation.py:95`

**MRR** (mean reciprocal rank) — averages 1/(rank of the first correct hit). Rewards putting the
right chunk *first*, not merely somewhere in the list.

**Hybrid search / reranking** — combining keyword and vector retrieval, then reordering with a
second model. **Not built.**

---

## Models and agents

**Structured output** — constraining a model to emit JSON matching a schema. Reduces malformed
output; does not eliminate it, which is why the repair loop exists.

**Function / tool calling** — the model emitting a *request* to call a named tool with
arguments, rather than prose. AMOS validates those arguments against the schema **before**
executing anything — the model produced them, so they are untrusted input. `tools/base.py`

**Repair loop** — re-prompting a bounded number of times when output fails validation, then
failing with a typed error. Bounded, because unbounded is a cost and latency hole.
`agents/agent.py:85`

**Bounded agent loop** — a hard cap on tool-calling iterations. The cap *is* the termination
proof; without it, "keep going until done" has no guarantee of ever being done.
`agents/tool_agent.py:90`

**Allowlist** — enumerating what is permitted, so anything unanticipated fails **closed**. A
blocklist must anticipate every dangerous case and fails *open* when it misses one. Used for
tool names per agent, HTTP hosts, span attributes and metric labels.

**Critic / reflection** — a second agent that reviews the first's output and can force a retry.
AMOS's critic has **no tools**, deliberately: a critic that can fetch new evidence can always
find something justifying whatever it already concluded. `agents/critic.py`

**Routing** — choosing which specialist handles a task, and a number saying how often that
choice is right. `agents/router.py:57`

**LLM-as-judge** — using a model to score output no deterministic check can settle. Reported
separately and never averaged with deterministic scores, because the judge shares a model
family, training data and blind spots with the system it judges.

**Golden set** — a fixed set of inputs with known-good expectations, used as a regression gate.
AMOS's are small and self-authored, and every document quoting their numbers says so.

**Prompt injection** — text in a goal, a fetched page or a file that the model treats as
instructions. The defence is that **security is enforced in code that never reads model
output**: a payload can make the model *ask* for `delete_file`, and the registry still returns
`not_found`. `docs/13-security.md`

---

## Storage and operations

**Migration** — a versioned, reversible schema change kept in source control. AMOS's CI applies
them forwards *and* backwards, because a migration that only goes forward is a one-way door.
`migrations/versions/`

**Schema drift** — ORM models and the real database silently disagreeing. Caught by a test, not
by hope. `tests/integration/test_schema_drift.py`

**N+1 query** — loading a list, then issuing one more query per row. Fixed with eager loading.
`database/repository.py`

**Partial index** — an index over only the rows matching a condition, so it stays small enough
to remain cached. AMOS has three: claimable runs, keyed runs, current memories.

**Denormalisation** — storing a value redundantly to avoid a join. `llm_calls.run_id` exists so
assembling a trace is a filter rather than a multi-table join.

**Supersession** — marking an old fact superseded rather than deleting it, so "current" is a
deterministic query and the history stays auditable. `memory/semantic.py:126`

**Transactional rollback fixture** — running each test inside a transaction that is rolled back
afterwards. Faster than recreating a schema, and tests cannot see each other's rows.
`tests/integration/conftest.py`

**Trace / span** — a trace is one request end to end; a span is one operation inside it, with a
parent. AMOS's V0.1 request id became the correlating id, which is why V0.9 was the smallest
milestone in the project. `telemetry/tracing.py`

**Cardinality** — how many distinct values a label can take. An unbounded label (a goal, a user
id, a UUID) creates one time series per value and destroys a metrics backend. Guarded by a
closed allowlist. `telemetry/metrics.py:29`

**Modular monolith** — one deployable process with hard internal boundaries. What AMOS is.
**Not** a distributed system, and never described as one. ADR-004

**ADR** (architecture decision record) — context, options, decision, why, tradeoffs, and — the
part that keeps it honest — **Reconsider if**. A decision without that line is a preference.
[`03-architecture-decisions.md`](03-architecture-decisions.md)

**12-factor config** — configuration from the environment, never from code. A missing key fails
at **startup**, not on the first request an hour later. `config.py`

**Fake vs mock vs stub** — a fake is a working implementation with a shortcut (`FakeProvider`
returns scripted responses); a mock asserts on how it was called; a stub returns fixed values.
AMOS uses fakes, so no test touches the network (N-14).
