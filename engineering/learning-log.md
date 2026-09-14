# Learning Log

Concepts, why they exist, where they appear in AMOS, and what to read. **Links, not essays** —
the reading is the point. Status uses the scale in `docs/20-learning-roadmap.md`:
**Recognise → Explain → Apply → Defend.**

This file tracks **status per concept**. The full reading list — prerequisites, architecture,
every milestone, every algorithm — is `docs/24-study-plan.md`. Read that for *what* to read;
record *how far you got* here.

---

# V0.1 — pre-read

Read before implementation starts.

## ASGI and async Python
**Problem it solves:** an LLM call blocks for seconds; a threaded server burns a thread per
waiting request. Async frees the thread while waiting on I/O.
**In AMOS:** every provider call, every endpoint.
**Read:** <https://fastapi.tiangolo.com/async/> · <https://docs.python.org/3/library/asyncio.html>
**Answer before moving on:**
- When does `async def` make something *slower* than `def`?
- What happens if a blocking call is made inside an async endpoint?
**Status:** ⬜ Recognise

## Pydantic v2 validation
**Problem it solves:** model output is text and could be anything. Validation is the boundary
where "probably JSON" becomes a typed object or a caught error.
**In AMOS:** `AgentResponse`, settings, later tool arguments and plans.
**Read:** <https://docs.pydantic.dev/latest/concepts/models/> ·
<https://docs.pydantic.dev/latest/concepts/json_schema/>
**Answer:**
- Difference between parsing and validation?
- Why validate output from an LLM that was *asked* for that schema?
**Status:** ⬜ Recognise

## Structured output and function calling
**Problem it solves:** free text cannot be dispatched on. Structured output constrains the model
to a schema.
**In AMOS:** V0.1 responses; V0.2 tool selection.
**Read:** <https://ai.google.dev/gemini-api/docs/structured-output> ·
<https://ai.google.dev/gemini-api/docs/function-calling>
**Answer:**
- Does structured output *guarantee* valid JSON? If so, why does AMOS still have a repair loop?
**Status:** ⬜ Recognise

## Protocols and dependency injection
**Problem it solves:** the agent must not know which provider it is calling — for swapping
vendors, and more immediately for testing without a network.
**In AMOS:** `LLMProvider`, later `VectorStore`.
**Read:** <https://docs.python.org/3/library/typing.html#typing.Protocol> ·
<https://peps.python.org/pep-0544/>
**Answer:**
- Protocol versus ABC — why does AMOS use a Protocol here?
- How does this make `FakeProvider` possible without touching agent code?
**Status:** ⬜ Recognise

## Test fixtures and fakes
**Problem it solves:** tests hitting a rate-limited, non-deterministic, paid API are slow,
flaky and eventually blocked.
**In AMOS:** `FakeProvider` in every unit and integration test (N-14).
**Read:** <https://docs.pytest.org/en/stable/how-to/fixtures.html> ·
<https://fastapi.tiangolo.com/tutorial/testing/>
**Answer:**
- Fake versus mock versus stub?
- How do you test "the model returned malformed JSON twice, then valid JSON"?
**Status:** ⬜ Recognise

## 12-factor configuration
**Problem it solves:** secrets in code get committed; environment-specific values in code make
environments un-swappable.
**In AMOS:** `pydantic-settings`, `.env`, `.env.example`.
**Read:** <https://12factor.net/config> ·
<https://docs.pydantic.dev/latest/concepts/pydantic_settings/>
**Answer:**
- Why should a missing API key fail at startup rather than on the first request?
**Status:** ⬜ Recognise

---

> **Backfilled 2026-09-13.** Everything below V0.1 was a single table of concept *names* until
> this date — nine milestones built, with their concepts listed and never given a problem
> statement, a pointer into the code, a question, or a status. That is the gap
> `current-state.md` is describing when it says the advance gate has never been met, and it is
> why `docs/20-learning-roadmap.md`'s mechanism (pre-read → build → explain-back → gate) only
> ever ran for V0.1. Links are the verified ones already gathered in `docs/24-study-plan.md`;
> nothing here is new research, only the per-concept form this file is supposed to hold.

---

# V0.2 — Tools

## Function and tool calling
**Problem it solves:** free text cannot be dispatched on. Tool calling makes the model emit a
*structured request* — a name and arguments — that code can validate and execute.
**In AMOS:** `agents/tool_agent.py`, `tools/base.py`. The schema sent to the model is generated
from the Pydantic input model, so what the model is told and what the code validates cannot drift.
**Read:** <https://ai.google.dev/gemini-api/docs/function-calling> ·
<https://arxiv.org/abs/2210.03629> (ReAct — the loop's ancestor)
**Answer before moving on:**
- Why validate arguments the model produced, when the schema was *given* to the model?
- What happens when the model names a tool that does not exist?
**Status:** ⬜ Recognise

## Bounded agent loops
**Problem it solves:** "keep calling tools until done" has no guarantee of ever being done. The
iteration cap *is* the termination proof.
**In AMOS:** `agents/tool_agent.py:90`, `max_iterations=5`, raising `ToolLoopExhaustedError`.
**Read:** <https://arxiv.org/abs/2210.03629>
**Answer:**
- What exactly stops a tool-calling loop from running forever?
- Why is exhausting the cap a `502` and not a `500`?
**Status:** ⬜ Recognise

## Prompt injection, and allowlists as the defence
**Problem it solves:** a goal, a fetched page or a file can contain text the model treats as
instructions. Requirement N-12: tool output is *data*, never instructions.
**In AMOS:** `docs/13-security.md`. The governing rule is that **security is enforced in code
that never reads model output** — the registry, the sandbox, the host allowlist.
**Read:** <https://simonwillison.net/series/prompt-injection/> · <https://genai.owasp.org/llm-top-10/>
· <https://simonwillison.net/2023/Apr/25/dual-llm-pattern/>
**Answer:**
- Why is "the system prompt says to treat tool output as data" *not* a control?
- `test_prompt_injection_in_tool_output_does_not_change_permissions` assumes the model is fully
  compromised. Why is that the right thing to assume in the test?
**Status:** ⬜ Recognise

## Allowlist versus blocklist
**Problem it solves:** a blocklist must anticipate every dangerous case and fails **open** when
it misses one. An allowlist fails **closed**.
**In AMOS:** tool names per agent (`agents/registry.py:53`), HTTP hosts
(`tools/builtin/http_get.py:109`), span attributes and metric labels (`telemetry/`).
**Read:** <https://owasp.org/www-community/attacks/Server_Side_Request_Forgery>
**Answer:**
- Why does `endswith("github.com")` accept `evil-github.com`, and what does AMOS do instead?
- Why is the *resolved IP* checked as well as the hostname?
**Status:** ⬜ Recognise

---

# V0.3 — Persistence and trace

## Idempotency
**Problem it solves:** a retry that duplicates work is a bug, not a retry. A client that times
out and resubmits must not start a second run.
**In AMOS:** `idempotency-key` header → `api/persistence.py`, partial unique index on
`runs.idempotency_key`. **Not** yet at task level — the open gap, scheduled V1.1.
**Read:** <https://brandur.org/idempotency-keys> · <https://docs.stripe.com/api/idempotent_requests>
**Answer:**
- What happens if a request *succeeds* but the response is lost?
- Why is AMOS idempotent at submission and not at task level, and what breaks because of that?
**Status:** ⬜ Recognise

## Async ORM sessions, transactions and N+1
**Problem it solves:** holding a pooled connection across a multi-second LLM call exhausts the
pool; loading a list then querying per row multiplies round trips.
**In AMOS:** `database/engine.py`, `database/repository.py`. Execution happens **outside** any
transaction, deliberately; eager loading avoids N+1 on trace assembly.
**Read:** <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html> ·
<https://docs.sqlalchemy.org/en/20/orm/session_basics.html> ·
<https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html> ·
<https://docs.sqlalchemy.org/en/20/core/pooling.html>
**Answer:**
- Why would holding a transaction across the LLM call deadlock the pool at six concurrent goals?
- Flush versus commit?
**Status:** ⬜ Recognise

## Migrations, in both directions
**Problem it solves:** schema is code and belongs in version control. A migration that only goes
forward is a one-way door.
**In AMOS:** `migrations/versions/`, four of them; CI applies up → down → up.
**Read:** <https://alembic.sqlalchemy.org/en/latest/tutorial.html> ·
<https://alembic.sqlalchemy.org/en/latest/autogenerate.html>
**Answer:**
- V0.4 shipped an irreversible migration. What made it irreversible, and what prevents it now?
- What can autogenerate *not* see?
**Status:** ⬜ Recognise

---

# V0.4 — Planner and executor

## DAGs, cycle detection and topological order
**Problem it solves:** a plan is a dependency graph. A cycle means nothing can ever start, and
the model can produce one.
**In AMOS:** `orchestration/plan.py:102` (DFS cycle detection), `:79` (topological order) —
both run **before a row is written**.
**Read:** <https://en.wikipedia.org/wiki/Topological_sorting> ·
<https://docs.python.org/3/library/graphlib.html>
**Answer:**
- Why validate the plan before persisting it rather than failing during execution?
- Compare AMOS's detection with `graphlib.TopologicalSorter` — what does the stdlib give you?
**Status:** ⬜ Recognise

## State machines with an enforced transition table
**Problem it solves:** illegal transitions must **raise**, not warn. This is "LLMs handle
uncertainty, software handles guarantees" made executable.
**In AMOS:** `orchestration/state.py` is the **only** module that changes a task's state.
**Read:** the module itself, then `docs/11-orchestration.md`.
**Answer:**
- Why can the LLM not move a task between states?
- Why does a retry return to `READY` rather than to a retry-specific state?
- `TIMED_OUT` existed for six milestones and was unreachable. What class of test would have
  caught that, and why did the exhaustive transition tests not?
**Status:** ⬜ Recognise

## Exponential backoff with full jitter
**Problem it solves:** retrying at fixed intervals synchronises clients into stampedes;
retrying forever is a cost hole.
**In AMOS:** `orchestration/retry.py:28`.
**Read:** <https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/>
**Answer:**
- What does jitter prevent that backoff alone does not?
- What is *full* jitter, as opposed to equal or decorrelated?
**Status:** ⬜ Recognise

## Partial success as a real outcome
**Problem it solves:** three of four tasks succeeding produced value. Forcing that into binary
success/failure either discards good work or overstates what happened.
**In AMOS:** `RunOutcome.PARTIALLY_COMPLETED`, `orchestration/executor.py`.
**Answer:**
- What happens to the dependents of a failed task, and why is the propagation a *fixed-point*
  loop rather than one pass?
**Status:** ⬜ Recognise

---

# V0.5 — Retrieval

## Chunking
**Problem it solves:** a document is too big to embed usefully. Too small loses context, too
large dilutes relevance.
**In AMOS:** `rag/chunking.py:49` — heading-aware, then size-bounded windows with overlap.
**Read:** <https://www.pinecone.io/learn/chunking-strategies/> ·
<https://github.com/FullStackRetrieval-com/RetrievalTutorials>
**Answer:**
- Why is the heading prepended to each of its chunks?
- What does overlap buy, and what does it cost?
**Status:** ⬜ Recognise

## Embeddings, cosine distance, and MRL truncation
**Problem it solves:** pgvector's HNSW index tops out at 2000 dimensions and the model emits
3072 — so the default embedding is *unindexable*.
**In AMOS:** `rag/embeddings.py` — truncate to 1536, then **re-normalise**. ADR-008.
**Read:** <https://ai.google.dev/gemini-api/docs/embeddings> · <https://arxiv.org/abs/2205.13147>
· <https://github.com/pgvector/pgvector>
**Answer:**
- What exactly breaks if you truncate and skip re-normalisation — and why is it so hard to
  notice?
- Why does the query use a different `task_type` from the document?
**Status:** ⬜ Recognise

## HNSW and approximate nearest neighbour
**Problem it solves:** exact nearest-neighbour search over every vector is a full scan.
**In AMOS:** `migrations/versions/5a881f4bdb98_*`, built **after** the corpus is loaded.
**Read:** <https://www.pinecone.io/learn/series/faiss/hnsw/> · <https://arxiv.org/abs/1603.09320>
· <https://supabase.com/blog/increase-performance-pgvector-hnsw>
**Answer:**
- What does "approximate" cost you, concretely?
- Why does the query have to use the `<=>` operator, and what happens silently if it does not?
**Status:** ⬜ Recognise

## Measuring retrieval: recall@k, strict recall, MRR
**Problem it solves:** "we have a vector database" is not a quality claim.
**In AMOS:** `rag/evaluation.py:95`; numbers in `docs/10-rag-architecture.md`.
**Read:** <https://en.wikipedia.org/wiki/Mean_reciprocal_rank> · <https://arxiv.org/abs/2401.05856>
(seven failure points — read before claiming RAG works)
**Answer:**
- Why report strict *and* lenient recall permanently, rather than picking one?
- recall@1 was measured at 50% and the explanation was not "retrieval is bad". What was it?
**Status:** ⬜ Recognise

## Grounding and refusal
**Problem it solves:** given nothing relevant, a model answers from its own memory and presents
it as grounded. The failure is invisible because the output looks identical.
**In AMOS:** `rag/retrieval.py` returns an explicit refusal instruction, never an empty list.
**Read:** <https://arxiv.org/abs/2005.11401> (the original RAG paper)
**Answer:**
- Why is refusing scored as a *success* in the evaluation suite?
**Status:** ⬜ Recognise

---

# V0.6 — Memory

## The memory taxonomy, and what belongs where
**Problem it solves:** "memory" in an AI system usually means "embed everything", which is wrong
for facts: exact recall is a key lookup, contradictions need ordering, provenance is a join.
**In AMOS:** `docs/09-memory-architecture.md`. Semantic = `memories` table; episodic = two
columns on `runs`; **working, conversation and knowledge-graph memory are not built**.
**Read:** <https://arxiv.org/abs/2310.08560> (MemGPT) · <https://arxiv.org/abs/2304.03442>
(generative agents) · <https://arxiv.org/abs/2309.02427> (CoALA — a taxonomy to argue *against*)
**Answer:**
- Why is there no `episodes` table?
- Which of the five memory kinds does AMOS actually have, and why were the others not built?
**Status:** ⬜ Recognise

## Contradiction resolution by supersession
**Problem it solves:** when a fact changes, the old one must stop being current — and vector
search has no notion of "superseded".
**In AMOS:** `memory/semantic.py:126`. Newest wins; superseded rows are **kept**, so "current"
is `superseded_by IS NULL` and the history stays auditable.
**Answer:**
- Why is this a *rule* rather than something the model decides?
- Why keep the old rows at all?
**Status:** ⬜ Recognise

## Claiming versus doing
**Problem it solves:** the model can say "I've remembered that" without the tool having been
called. Measured at a 38% false-claim rate before the fix.
**In AMOS:** `memory/reconcile.py:153`; measured by `make memory-trials`.
**Answer:**
- The root cause was not a prompt problem. What was it, and why did three prompt fixes fail to
  reach it? (`engineering/bugs-log.md`, 2026-09-10)
**Status:** ⬜ Recognise

---

# V0.7 — Multi-agent

## Specialisation as a structural property
**Problem it solves:** three prompts are not three agents. A prompt saying "you have no
calculator" is a request; a registry that does not contain one is a guarantee.
**In AMOS:** `agents/registry.py:53` builds a filtered registry per agent; disallowed tools
return `NOT_FOUND` because they do not exist.
**Read:** <https://www.anthropic.com/engineering/multi-agent-research-system> (a real one, with
the costs stated) · <https://arxiv.org/abs/2308.08155>
**Answer:**
- Why must the two routable allowlists be *disjoint*?
- Why does the instruction have to match the allowlist?
**Status:** ⬜ Recognise

## Critic and reflection
**Problem it solves:** an unreviewed answer has no quality gate.
**In AMOS:** `agents/critic.py` — **no tools**, bounded revise loop, and a broken critic
*accepts*.
**Read:** <https://arxiv.org/abs/2303.17651> (Self-Refine) · <https://arxiv.org/abs/2303.11366>
(Reflexion)
**Answer:**
- Why does giving the critic tools make its verdict unfalsifiable?
- What stops a critic/producer pair looping forever?
- Why does a broken critic accept rather than reject?
**Status:** ⬜ Recognise

## Structured inter-agent messages
**Problem it solves:** free-form chatter between agents cannot be validated, routed or traced.
**In AMOS:** `agents/messages.py` — **defined and not yet used**; the orchestrator assigns all
work. Scheduled for V1.3.
**Read:** <https://a2a-protocol.org/latest/>
**Answer:**
- What does a structured message buy over prose, concretely?
- What has to be bounded before agents may delegate to each other?
**Status:** ⬜ Recognise

---

# V0.8 — Asynchronous execution

## `SKIP LOCKED`
**Problem it solves:** without it, N workers running the same claim query block on each other's
locked rows and serialise into one.
**In AMOS:** `worker/queue.py:82`. The claim and the state change are **one statement in one
transaction**, so a dead worker's row is released by Postgres rather than by recovery code.
**Read:** <https://www.postgresql.org/docs/current/sql-select.html> ·
<https://www.2ndquadrant.com/en/blog/what-is-select-skip-locked-for-in-postgresql-9-5/> ·
<https://brandur.org/job-drain>
**Answer:**
- What exactly does `FOR UPDATE` lock, and what does `SKIP LOCKED` change?
- Why is `ORDER BY created_at` a preference rather than a guarantee under contention?
**Status:** ⬜ Recognise

## At-least-once delivery, and why exactly-once is unavailable
**Problem it solves:** knowing what you can and cannot promise. A worker can finish and die
before recording that it finished.
**In AMOS:** stated explicitly in `docs/12-event-system.md`; the mitigation is idempotent work.
**Read:** <https://bravenewgeek.com/you-cannot-have-exactly-once-delivery/> ·
<https://en.wikipedia.org/wiki/Two_Generals%27_Problem> ·
<https://microservices.io/patterns/communication-style/idempotent-consumer.html>
**Answer:**
- Why is exactly-once impossible, and what do you do instead?
- `remember_fact` could store a fact twice and it is harmless. Why is "harmless by luck" still
  recorded as a gap?
**Status:** ⬜ Recognise

## Visibility timeouts
**Problem it solves:** nothing has to *detect* that a worker died — only that a run has been
held too long.
**In AMOS:** `worker/queue.py:132`, default 600s. ADR-009 puts the task timeout beneath it.
**Read:** <https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html>
**Answer:**
- What happens to a worker that is merely *slow*?
- Why must the task timeout sit below the visibility timeout?
**Status:** ⬜ Recognise

## Poison messages
**Problem it solves:** a run that crashes every worker would be reclaimed forever, starving the
queue.
**In AMOS:** `attempt_count < max_attempts` in the claim query; `give_up()` at
`worker/queue.py:183`. **No dead-letter queue** — scheduled V1.1.
**Answer:**
- Without the attempt ceiling, what does one bad run do to every good one?
**Status:** ⬜ Recognise

---

# V0.9 — Observability

## Traces, spans and the correlating id
**Problem it solves:** answering "what happened on this request?" across processes.
**In AMOS:** `telemetry/tracing.py`. V0.1's request id became the correlating id, which is why
this was the smallest milestone in the project. **Trace context does not propagate into
workers** — a queued run is a separate trace, scheduled V1.1.
**Read:** <https://opentelemetry.io/docs/concepts/signals/traces/> ·
<https://opentelemetry.io/docs/languages/python/> ·
<https://opentelemetry.io/docs/specs/semconv/gen-ai/>
**Answer:**
- Trace versus log versus metric — when is each the right tool?
- Why did threading a request id at V0.1, for debugging, make V0.9 cheap?
**Status:** ⬜ Recognise

## Cardinality
**Problem it solves:** an unbounded label creates one time series per value and destroys a
metrics backend.
**In AMOS:** `telemetry/metrics.py:29` — a closed allowlist, not a blocklist.
**Read:** <https://sre.google/sre-book/monitoring-distributed-systems/> (golden signals) ·
<https://opentelemetry.io/docs/concepts/sampling/>
**Answer:**
- Which attributes would blow up cardinality here, and why is an allowlist the right shape?
- Goal text is excluded from spans by default. Why is opt-*in* the only safe direction?
**Status:** ⬜ Recognise

---

# V1.0 — Evaluation

## Golden sets and regression gating
**Problem it solves:** "it seems better" is not a measurement.
**In AMOS:** `evaluation/cases.py`, `rag/evaluation.py`, `agents/router.py`. All three sets are
**small and self-authored**, and every document quoting their numbers says so.
**Read:** <https://hamel.dev/blog/posts/evals/> · <https://eugeneyan.com/writing/evals/>
**Answer:**
- What makes a good golden set, and what is wrong with one written by the person who built the
  system?
- Which metrics were deliberately *not* optimised, and why?
**Status:** ⬜ Recognise

## LLM-as-judge, and its limits
**Problem it solves:** string overlap cannot tell a correct paraphrase from a fabrication.
**In AMOS:** `evaluation/judge.py:74` — one metric only (groundedness), reported separately and
**never averaged** into deterministic scores.
**Read:** <https://arxiv.org/abs/2306.05685> · <https://arxiv.org/abs/2303.16634> ·
<https://docs.ragas.io/en/stable/>
**Answer:**
- Why is a judge sharing a model family with the system it judges a problem?
- Why is it never averaged in with the deterministic metrics?
- What would a groundedness score of 1.00 actually be worth without human calibration?
**Status:** ⬜ Recognise

## Unmeasurable is not failed
**Problem it solves:** scoring an infrastructure limit as a quality defect makes part of every
score a measurement of the free tier.
**In AMOS:** `evaluation/metrics.py` — rate-limited cases are `unmeasurable`, excluded from
rates and from the CI gate.
**Answer:**
- A refusal case once scored 0/1, apparently the worst possible failure. What was actually wrong?
- What is the general lesson about a crude metric and a real failure?
**Status:** ⬜ Recognise

---

# Session-level lessons

## Verify, do not recall (Session 1)
Three assumed facts were checked and all three were wrong: the Gemini SDK package name (the
recalled one is deprecated), the current model IDs (a whole major version stale), and pgvector's
index dimension limit. The third would have surfaced only at V0.5 — after an entire corpus had
been embedded at an unindexable dimension.

**Rule adopted:** check current documentation before adopting or upgrading anything. Never write
a version number, model ID or API signature from memory.
**Status:** ✅ Defend

# V1.1 — Reliability

## Checkpointing and resumable work
**Problem it solves:** "recovered" meant "re-executed from the start", which was safe only
because every tool happens to be read-only — a property AMOS does not control for.
**In AMOS:** `orchestration/executor.py` (`TaskCheckpoint`), `database/progress.py`. The plan is
persisted when validated; each task is checkpointed at its terminal transition.
**Read:** ADR-010 · <https://docs.temporal.io/evaluate/understanding-temporal> (durable execution
— what AMOS approximates by hand)
**Answer before moving on:**
- Why was resumption impossible before V1.1, given that `GET /v1/runs/{id}` already returned a
  task DAG?
- Why can a resumed run not simply call the planner again and skip what succeeded?
- Why is the checkpoint a Protocol rather than a database call inside the executor?
**Status:** ⬜ Recognise

## Narrowing a window versus closing it
**Problem it solves:** knowing precisely what a reliability feature bought. Resumption is **not**
exactly-once and is not an attempt at it.
**In AMOS:** `docs/12-event-system.md`. Duplicate work went from a whole run to a single task —
a worker dying between finishing a task and checkpointing it still redoes that task.
**Read:** <https://en.wikipedia.org/wiki/Two_Generals%27_Problem> ·
<https://bravenewgeek.com/you-cannot-have-exactly-once-delivery/>
**Answer:**
- Why can the gap between doing work and recording it never be closed by code on one side of it?
- What would task-level idempotency keys add that resumption does not — and why are they *weaker*
  on a quota-limited system?
**Status:** ⬜ Recognise

## Dead-letter queues
**Problem it solves:** V0.8 had the poison-message *ceiling* and not the *queue* — give-ups were
marked FAILED and nothing collected them, so they were indistinguishable from ordinary failures.
**In AMOS:** `worker/queue.py` (`DEAD_LETTER`, `list_dead_letter`), `GET /v1/runs/dead-letter`.
**Read:** <https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html>
**Answer:**
- What is the difference between a run that FAILED and one that was dead-lettered, and why does
  collapsing them lose the one that needs attention?
- Why did this need no migration and no second table?
**Status:** ⬜ Recognise

## W3C trace context propagation
**Problem it solves:** a queued run was two unlinked traces — one for the submitting request, one
started fresh by the worker.
**In AMOS:** `telemetry/tracing.py` (`current_trace_context`, `continued_trace`),
`runs.trace_parent`.
**Read:** <https://www.w3.org/TR/trace-context/> ·
<https://opentelemetry.io/docs/concepts/context-propagation/>
**Answer:**
- Why is the context captured at *enqueue* rather than when the run row is created?
- What should happen to a run enqueued while tracing was disabled, and why is that the honest
  answer rather than a gap?
**Status:** ⬜ Recognise

## Session-level lessons

### A capability can be built, tested, and wired to nowhere (Session 11)
The worker never called `set_current_run_id`. Both the plan store and the task checkpoint read the
run id from context, so V1.1's resumption would have been **inert inside the worker** — the only
place a reclaim happens, and therefore the only place it mattered. Unit tests could not see it,
because they construct the pieces directly and never exercise the wiring.

This is the same shape as the `remember_fact` bug (Session 9): the capability worked and the
wiring did not.
**Rule adopted:** for any feature that reads ambient context, write one test that exercises the
*real entry point* — not the component.
**Status:** ⬜ Recognise

### Process-global state set inside a test is a hazard to every test after it (Session 11)
An integration test called `set_tracer_provider`, which OpenTelemetry honours **once per process**
and ignores thereafter with a warning. It silently blinded five telemetry unit tests that ran
after it: they captured no spans and failed asserting on spans that had genuinely been created.
**Rule adopted:** run the whole suite before committing, not the files touched. The failures were
in a directory the change never opened.
**Status:** ⬜ Recognise

# V1.2 — Evaluation credibility

## What a golden set can and cannot tell you
**Problem it solves:** "it seems better" is not a measurement — but a small self-authored set is
not a characterisation of quality either, and treating it as one is the more dangerous error.
**In AMOS:** `evaluation/cases.py`, `rag/evaluation.py`, `agents/router.py`. V1.2 made all three
larger and harder; none of them independent.
**Read:** <https://hamel.dev/blog/posts/evals/> · <https://eugeneyan.com/writing/evals/>
**Answer before moving on:**
- Why does enlarging a set authored by one person not fix the independence problem?
- The suite cost 21252 tokens at six cases and 40852 at nine. What does that imply about the
  *method*, not just the budget?
**Status:** ⬜ Recognise

## Adversarial testing under a compromised-model assumption
**Problem it solves:** a real-model test that passes because the model resisted the injection
tells you about that model on that day.
**In AMOS:** `tests/unit/rag/test_adversarial_retrieval.py` — the fake provider **complies** with
every attack.
**Read:** <https://simonwillison.net/series/prompt-injection/> · <https://genai.owasp.org/llm-top-10/>
**Answer:**
- Why is a fake that obeys the attacker a *stronger* test than a real model that refuses?
- What is the rule for deciding whether something is an adversarial test or an eval case?
**Status:** ⬜ Recognise

## Regression gating
**Problem it solves:** a score printed and discarded makes "did that change make things worse?"
unanswerable.
**In AMOS:** `evaluation/baseline.py`, `engineering/eval-baseline.json`.
**Answer:**
- Why is the baseline a committed file rather than a database table?
- Why must the judged metric never gate?
- Why does a different model or corpus report "not comparable" rather than a regression?
**Status:** ⬜ Recognise

---

# V1.3 — Delegation

## Structured contracts versus free-form handoff
**Problem it solves:** a natural-language handoff is unparseable, unvalidatable and untestable.
When the receiver misunderstands, there is nothing to point at — no field was wrong, because there
were no fields.
**In AMOS:** `agents/messages.py` (`AgentTask`), `agents/delegation.py`.
**Read:** <https://a2a-protocol.org/latest/> ·
<https://www.anthropic.com/engineering/multi-agent-research-system>
**Answer before moving on:**
- Why is delegation a *tool* rather than a new mechanism?
- What does the structured contract change about the failure mode?
**Status:** ⬜ Recognise

## A bound enforced by absence
**Problem it solves:** a depth check *inside* a tool is code that model output flows into, and can
be argued past. A tool that is not in the registry cannot be.
**In AMOS:** `agents/team.py` — past the cap, `delegate` is not built into the registry at all.
**Answer:**
- Why does this mean cycles need no separate detection?
- Where else in AMOS is a guarantee enforced by something's absence rather than by a check?
**Status:** ⬜ Recognise

## Privilege boundaries between agents
**Problem it solves:** delegation that moved *authority* would be a privilege-escalation path
dressed as a feature.
**In AMOS:** the delegate is built from its own `AgentSpec`, never the caller's.
**Answer:**
- What could a prompt injection achieve if a delegate inherited the caller's tools?
- Why does strict specialisation *depend* on delegation existing?
**Status:** ⬜ Recognise

## Session-level lesson

### A guard that keeps firing is doing someone else's job (Session 12)
`test_the_version_matches_the_latest_git_tag` caught the same class of mistake **three times in
one session**: v1.1 tagged at 1.0.0, v1.2 tagged at 1.1.0, and the original V1.0 instance it was
written for. Each time I fixed the value.

The third catch was the signal. The problem was never the version — it was that *bump, verify,
commit, tag* is four steps in a required order performed from memory. `make release VERSION=x.y.z`
now does all four and refuses to tag if `make check` fails.
**Rule adopted:** when a test catches the same class twice, fix the process, not the instance.
**Status:** ⬜ Recognise

---

# V1.4 — Authentication and isolation

## Authentication versus authorization
**Problem it solves:** knowing which of the two you have. "We have auth" routinely means both,
and AMOS has only the first.
**In AMOS:** `auth.py` establishes *who*. Nothing establishes *what they may do* — every
authenticated user can call every endpoint.
**Read:** <https://owasp.org/www-project-top-ten/> (A01 is broken access control) ·
<https://datatracker.ietf.org/doc/html/rfc7235>
**Answer before moving on:**
- Which one does `docs/13-security.md` still mark ❌, and why is that the gap most likely to be
  assumed solved?
**Status:** ⬜ Recognise

## A failure mode that returns *more* data
**Problem it solves:** most bugs announce themselves. A missing `WHERE user_id = ...` does not
raise, does not log, and returns more rows — so it presents as a working feature.
**In AMOS:** why enforcement is at *construction* (`RunRepository(session, actor)`) rather than a
`user_id` argument per method.
**Answer:**
- Why is "pass the user id to each method" one forgotten argument away from a leak, in a version
  that still compiles?
- Why is `recall_similar` more dangerous than `recall_exact` if the filter is missing?
- What would row-level security buy, and what would it cost on a pooled connection?
**Status:** ⬜ Recognise

## Credential storage
**Problem it solves:** a database that leaks should not also hand over access.
**In AMOS:** SHA-256 hashes; lookup by hash, so the plaintext exists only in the request.
**Read:** <https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html>
**Answer:**
- Why is a *random high-entropy API key* hashed with plain SHA-256, when a password would need
  bcrypt/argon2? (Hint: what makes a password hash slow, and why does that not apply here?)
- Why does `create_user` return the plaintext, and why only once?
**Status:** ⬜ Recognise

## Backfilling a NOT NULL column
**Problem it solves:** `ADD COLUMN ... NOT NULL` fails on any table with rows in it.
**In AMOS:** `migrations/versions/835121ee2bd2_*` — create users, insert an owner, add nullable,
backfill, then tighten.
**Answer:**
- Why is the order load-bearing?
- Why is `documents.user_id` deliberately *not* backfilled?
**Status:** ⬜ Recognise

## Session-level lesson

### A symptom can look nothing like its cause (Session 14)
Defining the auth dependency inside `create_app` made every protected endpoint answer **422**
instead of 401. `from __future__ import annotations` turns annotations into strings, and FastAPI
resolves them against *module* globals — where a function-local alias does not exist — so `actor`
was treated as an ordinary query parameter and failed validation.

Nothing about "422 on a request with no auth header" points at scoping of a type alias.
**Rule adopted:** when a framework's behaviour is inexplicable, check what it can actually *see*
at runtime — deferred annotations mean the name you wrote is not the object it resolves.
**Status:** ⬜ Recognise

### Exit codes and monitors both lie by omission (Session 15)
`python -m amos.rag.cli ingest docs | tail -15` finished with **exit 0** while Python had crashed on
a quota error — a pipeline reports its *last* command's status. Separately, a monitor counting rows
from its own connection saw zero for a quarter of an hour, because the ingest's rows were
uncommitted; it called a stall that was really a missing transaction boundary.
**Read:** <https://www.gnu.org/software/bash/manual/html_node/Pipelines.html> ·
<https://www.postgresql.org/docs/current/transaction-iso.html>
**Answer:**
- What does `set -o pipefail` change, and why would `tail` hide a failure without it?
- Why can a second connection not see rows inserted by an open transaction, and what does that imply
  for monitoring a long-running job by counting rows?
**Status:** ⬜ Recognise
