# 19 — Roadmap

Ten milestones. Each leaves the repository runnable, tested, demoable, documented and
committed. The **STOPPING POINT** on each answers the only question that matters if development
ends: *what can be shown, honestly?*

Ordering rationale for persistence preceding the planner is ADR-002.

```
V0.1 → V0.2 → V0.3 ─┬→ V0.4 ─┬→ V0.7 ─┐
                    │        │        ├→ V0.9 → V1.0
                    ├→ V0.5 →┴ V0.6 ──┘
                    └→ V0.8
```
V0.3 is the hinge — persistence independently unblocks the planner, RAG and async execution.

---

## V0.1 — Grounded Agent API

**Objective** A typed, tested LLM service with a provider seam and validated output.
**User capability** POST a goal, receive a schema-valid structured answer with a request id.
**Architecture** `Client → FastAPI → Agent → LLMProvider → Gemini`. No database (ADR-006).
**Technologies** Python 3.14, FastAPI ≥0.119.1, Pydantic ≥2.12, pydantic-settings,
`google-genai` ≥2.21.0, pytest, ruff, mypy.

**Learn** ASGI and async Python · Pydantic v2 validation · structured output · `Protocol` and
dependency injection · pytest fixtures and fakes · 12-factor config. Links in
`engineering/learning-log.md`.

**Implementation** `LLMProvider` protocol + `GeminiProvider` · `AgentResponse` Pydantic model ·
validate-and-repair loop with bounded re-prompting · settings from environment · structured JSON
logging with request id, model, latency, tokens, retries · typed error hierarchy.

**Tests** Unit: schema validation, repair loop with a `FakeProvider` returning malformed JSON
then valid, timeout handling, config loading. Integration: API contract, error status mapping.
One live smoke test, skipped without an API key. **No test touches the network** (N-14).

**Demo** `curl -X POST localhost:8000/v1/goals -d '{"goal":"..."}'` → structured JSON; logs show
the request id, token count and latency.

**Definition of Done** Tests pass · app starts clean · demo works · README run instructions ·
`current-state.md`, `session-log.md`, `learning-log.md` updated · resume evidence recorded ·
committed, pushed, tagged `v0.1` · repo flipped public.

**Failure modes handled** Provider timeout · malformed JSON → bounded repair → typed error ·
missing API key fails fast at startup, not on first request · rate limit surfaced as 429.

**Resume value** "Built a typed LLM service with provider abstraction, schema-validated
structured outputs, and bounded recovery from malformed model responses."
**Interview value** Why a protocol and not an if/else on provider name · why validate model
output at all · what happens on the third malformed response · why tests never hit the network.
**Future extension** Additional providers; streaming responses.

> **STOPPING POINT** — A working, tested API service demonstrating provider abstraction, schema
> validation and deliberate error handling. Modest but genuinely complete, and honestly
> describable as backend engineering with an LLM in it.

---

## V0.2 — Tool Registry

**Objective** The agent autonomously selects and executes validated tools.
**User capability** Goals requiring computation or lookup are answered using real tools rather
than model recall.
**Architecture** Adds `Tool` abstraction, registry, and a bounded agent loop.
**Technologies** Gemini function calling; `httpx` for the HTTP tool.

**Learn** Function/tool calling · JSON Schema · bounded agent loops and why they must be bounded
· allowlists as a prompt-injection defence · timeouts per call.

**Implementation** `Tool` base (name, description, input/output schema, timeout, permissions) ·
decorator-based registry · schema export to the provider · loop: model selects → **validate
args against schema** → execute with timeout → feed result back → repeat to a hard iteration
cap. Tools: `calculator` (pure), `http_get` (domain **allowlist**), `read_file` (sandboxed
path). All deterministic, all reversible — no payments, no deletion, no sending (brief §13).

**Tests** Contract test per tool (schema honoured, timeout respected) · agent loop with fakes ·
hallucinated tool name → typed error, not a crash · invalid arguments → rejected before
execution · loop cap enforced · `read_file` path traversal rejected · `http_get` off-allowlist
rejected.

**Demo** "What is 17% of 2,340 plus the number of days until New Year?" → correct answer via
tool calls, each visible in logs.

**Definition of Done** As V0.1, plus `docs/08-tool-specification.md` and
`docs/13-security.md` written, tagged `v0.2`.

**Failure modes handled** Hallucinated tool name · invalid arguments · tool timeout · tool
exception · infinite tool loop · path traversal · SSRF via allowlist.

**Resume value** "Tool-using agent with schema-validated arguments, per-tool timeouts and
permission allowlists."
**Interview value** What stops an infinite tool loop · why validate arguments when the model
produced them · how an allowlist mitigates prompt injection · why `read_file` is sandboxed.
**Future extension** MCP-compatible tool transport; human-approval gate for high-impact tools.

> **STOPPING POINT** — A genuine tool-using agent. The word "agent" becomes defensible here:
> it observes, decides and acts, with the system enforcing what it may touch.

---

## V0.3 — Persistence and Trace  ⟵ *the hinge*

**Objective** Every run durably recorded and fully inspectable.
**User capability** `GET /v1/runs/{id}` returns exactly what happened: every LLM call, tool
call, latency, token count and error.
**Architecture** Adds PostgreSQL 18 + pgvector (one Docker service), repository layer.
**Technologies** Docker Compose, PostgreSQL, SQLAlchemy 2.0 async, Alembic, asyncpg.

**Learn** Async SQLAlchemy sessions · migrations and why schema lives in version control ·
transactions and isolation · idempotency · connection pooling · why the trace is denormalised.

**Implementation** Schema per `docs/05-data-model.md` · repository layer isolating persistence
from domain · every V0.1/V0.2 in-memory record now persisted · `GET /v1/runs/{id}` ·
idempotency key on submit · Alembic baseline migration.

**Tests** DB integration tests with transactional rollback fixtures · trace completeness (every
LLM and tool call reachable from the run) · idempotent resubmit returns the original run ·
migration up/down.

**Demo** Run a goal, take the id, `GET /v1/runs/{id}`, read the complete story of that request.

**Definition of Done** As before, plus `docs/18-deployment.md`, `docker compose up` documented,
tagged `v0.3`.

**Failure modes handled** DB unavailable at startup → fail fast · connection lost mid-run →
run marked failed, not silently lost · duplicate submit → deduplicated.

**Resume value** "Durable execution history with full request tracing and idempotent goal
submission over PostgreSQL."
**Interview value** Why `run_id` is denormalised onto `llm_calls` · what an idempotency key
protects against · why Step is separate from Task · transactional test fixtures.
**Future extension** Trace UI; retention and archival policy.

> **STOPPING POINT** — Answers "what exactly happened on this request?" for any past run. Less
> flashy than a planner and considerably more convincing to an engineer.

---

## V0.4 — Planner and Executor

**Objective** Decompose a goal into a durable task DAG with deterministic state management.
**User capability** Multi-step goals are broken into tasks, executed in dependency order, with
failures retried and contained.
**Architecture** Adds Planner (LLM) and Executor (deterministic).

**Learn** DAGs and topological order · state machines · exponential backoff and jitter · why
partial success is a real outcome · separating proposal from execution.

**Implementation** Planner produces a `Plan`, validated as schema-correct **and acyclic** before
persistence · tasks stored per `docs/05-data-model.md` · executor walks ready tasks · state
machine per `docs/04-domain-model.md`, transitions enforced in code · bounded retry with
exponential backoff and jitter · `SKIPPED` propagation to dependents of failed tasks.

**Tests** Exhaustive state-machine transition tests, including every illegal transition ·
cyclic plan rejected · failing task retried then permanently failed · dependents skipped ·
`PARTIALLY_COMPLETED` produced when some tasks succeed and others do not.

**Demo** A goal needing three dependent steps: the trace shows the DAG, execution order, a
retry, and a coherent final answer.

**Definition of Done** As before, plus `docs/11-orchestration.md` and
`docs/17-failure-recovery.md`, tagged `v0.4`.

**Failure modes handled** Invalid or cyclic plan · task failure and retry exhaustion ·
dependency failure · partial completion · planner returning an empty plan.

**Resume value** "Goal decomposition into a persisted task DAG with an enforced state machine,
bounded retries with exponential backoff, and dependency-aware failure propagation."
**Interview value** Why the LLM cannot move a task between states · why jitter · what happens
when one of five tasks fails · how cycles are detected.
**Future extension** Re-planning on failure; parallel execution of independent tasks.

> **STOPPING POINT** — Real orchestration: goals become plans, plans become durable state, and
> failure is handled by design rather than by accident.

---

## V0.5 — Retrieval (RAG)

**Objective** A measured retrieval pipeline, not a vector database with a claim attached.
**User capability** Answers grounded in ingested documents, with citations; refusal when
nothing relevant is retrieved.
**Architecture** Adds ingestion pipeline, `VectorStore` protocol, `PgVectorStore`, retrieval as
a Tool — so the agent *chooses* to retrieve.

**Learn** Chunking strategies and their tradeoffs · embeddings and cosine distance · MRL
truncation and why re-normalisation is mandatory (ADR-008) · HNSW parameters · recall@k ·
grounding and refusal.

**Implementation** parse → chunk → metadata → embed (`gemini-embedding-001` @ 1536) → store ·
HNSW index built after load · retrieval tool with metadata filtering · answers cite chunk ids ·
empty retrieval → refuse rather than fabricate · **golden question set with measured recall@k**.

**Tests** Chunk boundary tests · embedding dimension and normalisation assertions · retrieval
returns known chunks for golden queries · citation ids resolve to real chunks · empty retrieval
produces refusal · recall@k regression gate.

**Demo** Ingest the AMOS docs; ask "why pgvector instead of Qdrant?"; get an answer citing
ADR-001. The system explains its own design from its own documents.

**Definition of Done** As before, plus `docs/10-rag-architecture.md` written **with measured
numbers**, tagged `v0.5`.

**Failure modes handled** Empty corpus · no relevant chunks · oversized document · embedding
API failure · re-ingest of unchanged content (content hash).

**Resume value** "Retrieval pipeline over pgvector with MRL-truncated embeddings, HNSW indexing,
citation-grounded answers and measured recall@k."
**Interview value** Chunk size choice and its cost · why 1536 not 3072 · what re-normalisation
fixes · how retrieval quality is *measured* rather than asserted · why retrieval is a tool.
**Future extension** Hybrid keyword+vector search; reranking; query rewriting.

> **STOPPING POINT** — RAG that earns the name: real pipeline, real citations, and a number
> attached to its quality.

---

## V0.6 — Memory

**Objective** Distinguish the five memory kinds and give each the right store.
**User capability** AMOS recalls stated preferences and learns from prior run outcomes.
**Learn** Memory taxonomy · what belongs in relational versus vector storage · fact extraction ·
staleness and contradiction.
**Implementation** Semantic memory (facts, embedded, reusing `VectorStore`) · episodic memory
(past run outcomes, retrieved by goal similarity) · working memory scoped to a Run · explicit
ADR on the placement of each.
**Tests** Fact recall across sessions · contradicting facts resolved deterministically ·
episodic retrieval surfaces relevant prior runs · working memory does not leak between runs.
**Demo** Tell AMOS a preference in one session; a later session applies it unprompted.
**Definition of Done** Plus `docs/09-memory-architecture.md`, tagged `v0.6`.
**Failure modes** Contradictory facts · unbounded memory growth · stale memory · wrong-tier
retrieval.
**Resume value** "Tiered memory with distinct semantic, episodic and working stores."
**Interview value** Why not vector search for everything · how contradictions resolve · what
makes episodic memory different from conversation history.

> **STOPPING POINT** — Persistent, tiered memory across sessions, with each tier's store
> justified rather than assumed.

---

## V0.7 — Multi-Agent

**Objective** Make "multi-agent" an honest word.
**User capability** Specialised agents collaborate on one goal; a critic gates the output.
**Learn** Agent specialisation · structured inter-agent messaging · critic/reflection patterns ·
routing accuracy.
**Implementation** Agent registry · Researcher, Analyst, Critic with distinct prompts, tools and
schemas · **structured A2A messages** (brief §10), never free-form chatter · critic validates
and can force a retry · router assigns tasks to agents.
**Tests** Routing accuracy on a labelled task set · critic rejects known-bad output · A2A
messages schema-validated · agent tool allowlists enforced.
**Demo** A research goal: Researcher gathers, Analyst synthesises, Critic rejects an
unsupported claim and forces a retry — all visible in the trace.
**Definition of Done** Plus `docs/07-agent-specification.md` completed, tagged `v0.7`.
**Failure modes** Misrouting · critic loops · one agent failing mid-collaboration · agent
exceeding its tool allowlist.
**Resume value** "Multi-agent orchestration with specialised agents, structured inter-agent
contracts and a critic validation gate."
**Interview value** What makes these agents genuinely different rather than three prompts · why
structured messages · what stops critic/producer looping forever.

> **STOPPING POINT** — The first point at which "multi-agent system" is defensible. Before this
> milestone it would be a lie, and it is not claimed anywhere earlier.

---

## V0.8 — Asynchronous Execution

**Objective** Long-running goals without blocking, with crash-safe job claiming.
**User capability** Submit a goal, get `202` and a run id immediately, poll for progress.
**Learn** At-least-once delivery · `SKIP LOCKED` · visibility timeouts · idempotent workers ·
why exactly-once is not available.
**Implementation** Worker process · Postgres `SKIP LOCKED` claim (ADR-003) · `202 Accepted` +
poll/SSE · visibility timeout reclaiming stuck tasks · idempotent task execution.
**Tests** Concurrent workers claim disjoint tasks · killed worker's task is reclaimed · no task
executes twice with visible effect · visibility timeout honoured.
**Demo** Submit a long goal, watch progress across workers, kill a worker mid-run, watch the
task get reclaimed and complete.
**Definition of Done** Plus `docs/12-event-system.md`, tagged `v0.8`.
**Failure modes** Worker crash · stuck task · duplicate execution · queue starvation.
**Resume value** "Asynchronous task execution with crash-safe job claiming via PostgreSQL
`SKIP LOCKED`, visibility timeouts and idempotent workers."
**Interview value** Why not Celery · why exactly-once is impossible and what to do instead ·
what `SKIP LOCKED` actually locks · how a dead worker's task is recovered.

> **STOPPING POINT** — Genuine asynchronous processing with real crash recovery. The strongest
> distributed-systems content in the project, without a distributed deployment.

---

## V0.9 — Observability

**Objective** A complete trace of any run, in standard tooling.
**Learn** OpenTelemetry traces/spans/attributes · sampling · metrics versus logs versus traces ·
cardinality.
**Implementation** OTel instrumentation · spans for LLM calls, tool calls, retrieval, task
execution · request id becomes `trace_id` · metrics: latency, tokens, retry rate, failure rate ·
local collector.
**Tests** Trace completeness · span parent/child correctness · no PII or secrets in attributes.
**Demo** One run visualised end to end as a waterfall, showing exactly where time went.
**Definition of Done** Plus `docs/14-observability.md`, tagged `v0.9`.
**Resume value** "Distributed tracing with OpenTelemetry across LLM, tool and retrieval calls."
**Interview value** Why the V0.1 request id made this cheap · trace versus log · what would
blow up cardinality.

> **STOPPING POINT** — Production-grade observability, and the payoff for threading a request
> id from the first milestone.

---

## V1.0 — Evaluation

**Objective** Quality measured, not asserted.
**Learn** LLM evaluation methodology · golden datasets · LLM-as-judge and its limits ·
regression gating.
**Implementation** Golden goal set · metrics: task completion, routing accuracy, tool-selection
accuracy, retrieval recall, groundedness, output validity, latency, token cost · CI regression
gate.
**Tests** The evaluation harness itself is tested · known-bad outputs score badly · scores are
reproducible.
**Demo** `make eval` prints a scorecard; a deliberately introduced regression fails CI.
**Definition of Done** Plus `docs/16-evaluation.md`, tagged `v1.0`.
**Resume value** "Evaluation harness measuring task completion, tool selection and retrieval
quality, gating regressions in CI."
**Interview value** How agent quality is measured at all · limits of LLM-as-judge · what makes a
good golden set · which metrics were deliberately *not* optimised.

> **STOPPING POINT** — A complete, measured, observable agent platform. Every claim in
> `docs/22-resume-evidence.md` backed by a file, a test and a number.

---

---

## V1.1 — Reliability

**Objective** Make at-least-once delivery honest rather than safe-by-luck.
**User capability** A reclaimed run resumes instead of redoing completed work; a run the queue
gave up on is collected for review rather than lost among ordinary failures; a queued run is one
trace end to end.
**Architecture** Adds a `TaskCheckpoint` and a `PlanStore` — two protocols the orchestration layer
defines and the database layer implements, so the executor still has no database access.
**Technologies** None new. PostgreSQL and the OpenTelemetry already present.

**Learn** Why exactly-once is unavailable and what narrowing the window buys · checkpointing and
resumable work · dead-letter queues and what they are *for* · W3C trace context propagation
across a process boundary.

**Implementation** Persist the plan when it is validated and checkpoint each task at its terminal
transition (ADR-010) · on reclaim, the stored rows **are** the plan — no second planning call,
because the planner is an LLM and would produce a different DAG · `DEAD_LETTER` as a status
distinct from `FAILED` · `GET /v1/runs/dead-letter` · `runs.trace_parent` captured at enqueue and
continued by the worker.

**Tests** A run with one task done and one failed is reclaimed: the done task is not re-run, the
failed one receives the stored answer as context, and the planner is never asked twice · a
dead-lettered run is not claimable · the dead-letter route is declared before `/v1/runs/{run_id}`
(FastAPI matches in declaration order) · a worker span is a child of the enqueuing span · a
failing checkpoint does not fail the run.

**Demo** Submit a goal asynchronously, kill the worker after the first task, start another: the
trace shows one continuous waterfall across both processes, and only the unfinished task runs.

**Definition of Done** As before, plus ADR-010, `docs/interview/reliability.md`, and
`12-event-system.md` / `17-failure-recovery.md` updated to say what is now closed.

**Failure modes handled** Worker death mid-run · a checkpoint failing · a stored plan ref that no
longer matches · a run enqueued before tracing existed · a poison run starving the queue.

**Resume value** "Resumable task execution across worker crashes, with a dead-letter path for
poison jobs and distributed trace context propagated across process boundaries."
**Interview value** Why re-planning on resume would be wrong · what resumption buys that an
idempotency key does not · why the checkpoint is a protocol rather than a database call · why
`DEAD_LETTER` is not `FAILED`.
**Future extension** Task-level idempotency keys; re-planning on failure.

> **STOPPING POINT** — Crash recovery stops resting on "every tool happens to be read-only". The
> window for duplicate work is one task rather than a whole run, and the residual gap is stated
> rather than papered over.

---

## V1.2 — Evaluation credibility

**Objective** Attack the suite's own stated weakness: self-authored cases, nothing adversarial,
and a score that is printed and discarded.
**User capability** A regression in answer quality fails a command instead of going unnoticed;
hostile content in the corpus is proven not to widen what the system can do.
**Architecture** No new components. A committed baseline file and a new test module.
**Technologies** None new.

**Learn** What a golden set is *for* versus what it cannot tell you · why a judged metric must not
gate · adversarial testing against a compromised-model assumption · regression gating.

**Implementation** Adversarial cases as **tests, not eval cases** (ADR-011) — a poisoned corpus
passage with a model that fully complies, asserting the registry, the permission refusal and the
per-agent allowlist all hold · `engineering/eval-baseline.json` compared by `make eval` and
written only by `make eval-baseline` · golden sets enlarged: goals 6→9, retrieval 12→16, routing
10→15, with the new cases chosen to be *hard* rather than more of the same.

**Tests** A regression fails the gate even when every case passes · a different model or corpus
reports "not comparable" rather than a false regression · the judged score never gates ·
comparison has no side effects · an injected tool name is `NOT_FOUND` with the model complying.

**Demo** `make eval` prints the scorecard and the delta against the stored baseline; edit a rate
in the baseline file and watch it fail.

**Definition of Done** As before, plus ADR-011 and `docs/16-evaluation.md` extended.

**Resume value** "Adversarial evaluation of an LLM agent under a compromised-model assumption,
and a committed-baseline regression gate separating deterministic from model-judged evidence."
**Interview value** Why adversarial cases are tests rather than eval cases · why a judged metric
must never gate · what a bigger golden set does and does not fix · why the baseline is a file.
**Future extension** Per-case history; human calibration of the judge; independently authored sets.

> **STOPPING POINT** — Quality claims survive hostile input and carry a stored history. The
> independence problem is untouched and still stated plainly, because enlarging a set authored by
> one person does not fix it.

---

## V1.3 — Agent-to-Agent Delegation

**Objective** Give `AgentTask` a caller, making the brief's §10 true rather than aspirational.
**User capability** A specialist that hits a capability it does not have hands that piece to the
agent that does, instead of guessing or failing.
**Architecture** Adds a `delegate` tool and a shared per-run budget. No new components.
**Technologies** None new.

**Learn** Structured inter-agent contracts versus free-form handoff · why a bound enforced by
*absence* beats a bound enforced by a check · privilege boundaries between agents · cost
amplification in multi-hop systems.

**Implementation** `delegate` as a Tool (ADR-012), inheriting schema validation, timeouts,
tracing and per-agent allowlisting · depth enforced by the tool being absent from the registry
past the cap · a budget shared across the run · self-delegation rejected before execution · the
delegate built from **its own** AgentSpec, never the caller's · the system instruction extended
only when the tool is present.

**Tests** Depth cap removes the tool rather than refusing the call · a delegation cycle
terminates · the budget is shared, not per-agent · an exhausted budget is a refusal the caller
can act on, not a failure · a delegate does not inherit the caller's tools · the end-to-end
researcher→analyst→researcher chain, with the researcher never acquiring a calculator.

**Demo** Ask for a percentage of a documented number: the researcher retrieves it, delegates the
arithmetic, and the trace shows the `delegate` call and the analyst's `calculator` call beneath it.

**Definition of Done** As before, plus ADR-012, `docs/interview/delegation.md`, and
`07-agent-specification.md` extended.

**Failure modes handled** Infinite delegation · cycles · self-delegation · an unknown target ·
budget exhaustion mid-task · a delegate failing · privilege escalation via delegation.

**Resume value** "Bounded agent-to-agent delegation over a validated message contract, with depth
and budget limits enforced structurally and no privilege inheritance between agents."
**Interview value** Why delegation is a tool · why the depth bound is the tool's absence rather
than a check · why the budget is shared · why a delegate uses its own allowlist · why two
distribution mechanisms are defensible.
**Future extension** Delegation-accuracy measurement; negotiation; clarifying questions.

> **STOPPING POINT** — Specialisation stays strict *because* delegation exists. Without it the
> pressure is to widen allowlists until they overlap, at which point routing accuracy stops
> meaning anything.

---

## Beyond V1.3

Chosen 2026-09-13, still needing its ADR before any code: **V1.4** authentication and multi-user
isolation.

Unscheduled, and only with a real driver: MCP tool transport · human-approval workflows · a web
UI · Temporal for durable workflows · Kubernetes · hybrid search and reranking · a per-run cost
budget · re-planning on failure.

None of these are promised, and none may appear on a resume until built.
