# 24 — Study Plan

Everything to read to make AMOS *yours* — from zero knowledge to defending any line of it.

Companion documents, do not duplicate them:
- [`20-learning-roadmap.md`](20-learning-roadmap.md) — the **mechanism** (pre-read → build →
  explain-back → interview doc → gate) and the Recognise/Explain/Apply/Defend scale.
- [`../engineering/learning-log.md`](../engineering/learning-log.md) — the **running status** of
  each concept.
- `docs/interview/*.md` — the **questions** that close each gate.

This document is the **reading list**: what to read, in what order, why it exists in this
codebase, and which file it explains.

---

## How to use this

**Priority tags.** The list is long; the required part is not.

| Tag | Meaning |
|---|---|
| **[core]** | Cannot defend the repo without it. Roughly 40 items. Non-negotiable. |
| **[deep]** | Needed to *extend* the system or answer a follow-up question. |
| **[ref]** | Bookmark, skim, return when the topic bites. |

**Rules that make this work**

1. Read **[core]** for a milestone *before* re-reading that milestone's code, not after.
2. After each item, open the named file and find the concept in it. Reading without the code
   attached is how this becomes trivia.
3. Stop at **Defend** only for things claimed in [`22-resume-evidence.md`](22-resume-evidence.md).
   **Explain** is enough for the rest.
4. A paper's abstract plus the figures is a legitimate read. Do not read every arXiv PDF in full.

**Honest total**: Tier 0 + Tier A + V0.1–V0.5 **[core]** is roughly **70–90 hours** for someone
starting from zero. The remaining tags are open-ended.

---

# Tier 0 — Prerequisites

Zero knowledge → able to read `src/amos/` without guessing. **~30 h.**

## 0.1 Python, the language

Where: everywhere. Every file.

| What | Why it is in AMOS | Read |
|---|---|---|
| Syntax, data model, modules, exceptions | The whole codebase | **[core]** <https://docs.python.org/3/tutorial/index.html> |
| Type hints, generics, `Protocol` | `mypy --strict` passes; the seams are typed | **[core]** <https://docs.python.org/3/library/typing.html> |
| `Protocol` / structural typing | `LLMProvider`, `VectorStore` — swap Gemini for a fake with no inheritance | **[core]** <https://peps.python.org/pep-0544/> |
| ABCs | `Tool` is an ABC *on purpose*, unlike the providers — `tools/base.py:94` | **[core]** <https://docs.python.org/3/library/abc.html> |
| `StrEnum` | `TaskState`, `Permission`, `Confidence` — enum values that serialise as strings | **[deep]** <https://docs.python.org/3/library/enum.html> |
| `contextvars` | Request id follows an async call chain without being an argument — `observability.py` | **[deep]** <https://docs.python.org/3/library/contextvars.html> |
| `ast` module | The calculator parses instead of `eval()`-ing — `tools/builtin/calculator.py:77` | **[deep]** <https://docs.python.org/3/library/ast.html> |

Answer before moving on: *why is `LLMProvider` a Protocol but `Tool` an ABC?*
(`docs/interview/agents.md` opens with exactly this.)

## 0.2 Async Python

Where: every provider call, every endpoint, the executor's concurrency.

| What | Read |
|---|---|
| The mental model — event loop, awaitables, tasks | **[core]** <https://realpython.com/async-io-python/> |
| Reference | **[core]** <https://docs.python.org/3/library/asyncio.html> |
| `asyncio.timeout`, `gather`, cancellation | **[core]** <https://docs.python.org/3/library/asyncio-task.html> |
| Why `async`/`await` exists at all | **[deep]** <https://peps.python.org/pep-0492/> |
| Long-form tutorials | **[ref]** <https://superfastpython.com/python-asyncio/> |

Answer: *what happens if a blocking call runs inside an async endpoint?* — and then find the
per-tool timeout in `tools/base.py`.

## 0.3 HTTP, REST and API surface

| What | Why | Read |
|---|---|---|
| HTTP fundamentals | The API is the product surface | **[core]** <https://developer.mozilla.org/en-US/docs/Web/HTTP> |
| Status codes | `502` for `OutputValidationError` is a deliberate choice — `api/app.py:54` | **[core]** <https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status> |
| ASGI | What FastAPI actually implements | **[deep]** <https://asgi.readthedocs.io/en/latest/> |
| Server-sent events | V0.8 progress streaming | **[ref]** <https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events> |

## 0.4 JSON Schema

Where: tool argument validation, structured output, plan validation.

- **[core]** <https://json-schema.org/learn/getting-started-step-by-step>
- **[core]** <https://docs.pydantic.dev/latest/concepts/json_schema/> — how a Pydantic model
  becomes the schema handed to the model.

## 0.5 SQL and PostgreSQL

Where: `database/`, `migrations/`, the whole trace.

| What | Read |
|---|---|
| Relational basics, joins, constraints | **[core]** <https://www.postgresql.org/docs/current/tutorial.html> |
| Indexes — what they cost, when they are used | **[core]** <https://use-the-index-luke.com/> |
| Transactions and isolation levels | **[core]** <https://www.postgresql.org/docs/current/transaction-iso.html> |
| MVCC — why readers do not block writers | **[deep]** <https://www.postgresql.org/docs/current/mvcc.html> |
| `EXPLAIN` | **[deep]** <https://www.postgresql.org/docs/current/using-explain.html> |
| Explicit locking (the V0.8 prerequisite) | **[deep]** <https://www.postgresql.org/docs/current/explicit-locking.html> |

## 0.6 Version control, containers, tooling

| What | Read |
|---|---|
| Git properly — branches, rebase, history as a document | **[core]** <https://git-scm.com/book/en/v2> |
| Containers | **[core]** <https://docs.docker.com/get-started/> |
| Podman (what `compose.yaml` is actually verified on) | **[core]** <https://docs.podman.io/en/latest/> |
| 12-factor config | **[core]** <https://12factor.net/config> |
| mypy | **[core]** <https://mypy.readthedocs.io/en/stable/> |
| ruff | **[core]** <https://docs.astral.sh/ruff/> |
| uv (faster than the pip flow in use; optional) | **[ref]** <https://docs.astral.sh/uv/> |
| GitHub Actions — the missing CI in the debt list | **[deep]** <https://docs.github.com/en/actions> |

## 0.7 Testing

Where: 314 tests, and the reason none of them touch the network (N-14).

| What | Read |
|---|---|
| pytest fixtures | **[core]** <https://docs.pytest.org/en/stable/how-to/fixtures.html> |
| Test structure and conventions | **[core]** <https://docs.pytest.org/en/stable/explanation/anatomy.html> |
| Fake vs mock vs stub — `FakeProvider` is a **fake** | **[core]** <https://martinfowler.com/articles/mocksArentStubs.html> |
| Shorter version of the same | **[core]** <https://martinfowler.com/bliki/TestDouble.html> |
| The test pyramid — why so few integration tests | **[deep]** <https://martinfowler.com/articles/practical-test-pyramid.html> |
| Property-based testing — a real option for the chunker | **[ref]** <https://hypothesis.readthedocs.io/en/latest/> |

---

# Tier A — Architecture and design

The part that makes the difference between "I built this" and "I designed this". **~15 h.**

## A.1 Layering, ports and adapters

AMOS is layered: `api → service → orchestration → agents → {llm, tools, rag} → database`.
Dependencies point inwards; the outer layers are swappable. That is not an accident, and it is
the reason `FakeProvider` and `InMemoryVectorStore` exist at all.

| What | Read |
|---|---|
| Hexagonal architecture, from the person who named it | **[core]** <https://alistair.cockburn.us/hexagonal-architecture/> |
| The same ideas *in Python*, free and excellent — the single most useful book here | **[core]** <https://cosmicpython.com/> |

## A.2 Repository, service layer, unit of work

`database/repository.py` is a repository. `api/persistence.py:31 RunService` is a service layer.
The session scope in `database/engine.py:45` is a unit of work.

| What | Read |
|---|---|
| Repository pattern | **[core]** <https://martinfowler.com/eaaCatalog/repository.html> |
| Repository, in Python | **[core]** <https://cosmicpython.com/book/chapter_02_repository.html> |
| Service layer | **[core]** <https://cosmicpython.com/book/chapter_04_service_layer.html> |
| Unit of work | **[core]** <https://cosmicpython.com/book/chapter_06_uow.html> |

Answer: *why does execution happen **outside** the transaction?*
(`docs/interview/persistence.md`.)

## A.3 Monolith vs microservices — and why AMOS is neither by accident

ADR-004 says modular monolith. Being able to argue this is worth more in an interview than any
framework name.

| What | Read |
|---|---|
| Monolith first | **[core]** <https://martinfowler.com/bliki/MonolithFirst.html> |
| The microservices definition being rejected | **[core]** <https://martinfowler.com/articles/microservices.html> |
| Pattern catalogue — the vocabulary | **[deep]** <https://microservices.io/patterns/monolithic.html> |
| System design breadth | **[ref]** <https://github.com/donnemartin/system-design-primer> |
| Cloud architecture patterns | **[ref]** <https://learn.microsoft.com/en-us/azure/architecture/patterns/> |
| Well-architected style frameworks | **[ref]** <https://cloud.google.com/architecture/framework> |

## A.4 Design records and diagrams

`docs/03-architecture-decisions.md` is already in Nygard's ADR format, including *Reconsider if*.

| What | Read |
|---|---|
| ADR practice and templates | **[core]** <https://adr.github.io/> |
| A large template collection | **[ref]** <https://github.com/joelparkerhenderson/architecture-decision-record> |
| C4 — how to draw the system at four zoom levels | **[deep]** <https://c4model.com/> |

## A.5 Domain modelling and state

`docs/04-domain-model.md` defines Run, Task, Step, Plan and the five memory kinds.
`orchestration/state.py` makes the state machine executable.

| What | Read |
|---|---|
| DDD, the short version | **[deep]** <https://martinfowler.com/bliki/DomainDrivenDesign.html> |
| Finite state machines | **[core]** <https://en.wikipedia.org/wiki/Finite-state_machine> |
| Statecharts — where to go when the FSM grows | **[deep]** <https://statecharts.dev/> |

## A.6 Designing for failure

The single most defensible thing in this repo.

| What | Read |
|---|---|
| Timeouts, retries, backoff, jitter — read this twice | **[core]** <https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/> |
| Cascading failure | **[core]** <https://sre.google/sre-book/addressing-cascading-failures/> |
| Overload | **[deep]** <https://sre.google/sre-book/handling-overload/> |
| Circuit breaker — deliberately *not* built; know why | **[deep]** <https://martinfowler.com/bliki/CircuitBreaker.html> |
| Saga — the alternative to the DAG executor for compensable work | **[ref]** <https://microservices.io/patterns/data/saga.html> |

## A.7 Agent architecture specifically

Read these to know where AMOS sits in the landscape, and to answer "why not LangChain?".

| What | Read |
|---|---|
| Workflows vs agents — the taxonomy AMOS follows | **[core]** <https://www.anthropic.com/engineering/building-effective-agents> |
| Twelve factors for agents | **[core]** <https://github.com/humanlayer/12-factor-agents> |
| What a graph framework gives you — the thing ADR says not to adopt | **[deep]** <https://langchain-ai.github.io/langgraph/concepts/why-langgraph/> |
| MCP — the V0.2 "future extension" | **[deep]** <https://modelcontextprotocol.io/> |

---

# Tier 1 — V0.1 · Grounded Agent API

**In the repo:** `llm/base.py`, `llm/gemini.py`, `llm/fake.py`, `agents/agent.py`,
`agents/schemas.py`, `api/app.py`, `config.py`, `errors.py`, `observability.py`.

**Algorithms:** bounded validate-and-repair loop (`agents/agent.py:85`); request-id propagation
through async context.

| Concept | Read |
|---|---|
| FastAPI, async endpoints | **[core]** <https://fastapi.tiangolo.com/async/> |
| Dependency injection | **[core]** <https://fastapi.tiangolo.com/tutorial/dependencies/> |
| Testing FastAPI | **[core]** <https://fastapi.tiangolo.com/tutorial/testing/> |
| Pydantic v2 models | **[core]** <https://docs.pydantic.dev/latest/concepts/models/> |
| Validators | **[deep]** <https://docs.pydantic.dev/latest/concepts/validators/> |
| Settings from environment | **[core]** <https://docs.pydantic.dev/latest/concepts/pydantic_settings/> |
| Gemini structured output | **[core]** <https://ai.google.dev/gemini-api/docs/structured-output> |
| The SDK actually imported | **[core]** <https://googleapis.github.io/python-genai/> |
| Model IDs and quotas — re-check, never recall | **[core]** <https://ai.google.dev/gemini-api/docs/models> · <https://ai.google.dev/gemini-api/docs/rate-limits> |
| Starlette / Uvicorn internals | **[ref]** <https://github.com/encode/starlette> · <https://github.com/encode/uvicorn> |

**Gate:** [`interview/foundation.md`](interview/foundation.md).

---

# Tier 2 — V0.2 · Tools

**In the repo:** `tools/base.py`, `tools/registry.py`, `tools/builtin/*`, `agents/tool_agent.py`.

**Algorithms:** bounded tool-calling loop with hard iteration cap (`tool_agent.py:90`);
AST-whitelist arithmetic instead of `eval` (`calculator.py:77`); path canonicalisation then
prefix check (`read_file.py`); host allowlist (`http_get.py`); schema validation of model-produced
arguments before execution.

| Concept | Read |
|---|---|
| Function calling | **[core]** <https://ai.google.dev/gemini-api/docs/function-calling> |
| The same idea, other vendors — the shape is portable | **[deep]** <https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview> · <https://platform.openai.com/docs/guides/function-calling> |
| ReAct — reason + act interleaved, the loop's ancestor | **[core]** <https://arxiv.org/abs/2210.03629> |
| Toolformer — models learning to call tools | **[ref]** <https://arxiv.org/abs/2302.04761> |
| Prompt injection — read the whole series eventually | **[core]** <https://simonwillison.net/series/prompt-injection/> |
| The dual-LLM pattern | **[deep]** <https://simonwillison.net/2023/Apr/25/dual-llm-pattern/> |
| OWASP Top 10 for LLM applications | **[core]** <https://genai.owasp.org/llm-top-10/> |
| SSRF — what the `http_get` allowlist prevents | **[core]** <https://owasp.org/www-community/attacks/Server_Side_Request_Forgery> |
| API security top 10 | **[ref]** <https://owasp.org/API-Security/editions/2023/en/0x11-t10/> |

**Gate:** [`interview/agents.md`](interview/agents.md).

---

# Tier 3 — V0.3 · Persistence and trace

**In the repo:** `database/models.py`, `database/engine.py`, `database/repository.py`,
`api/persistence.py`, `migrations/`.

**Algorithms:** idempotency-key deduplication on submit; eager loading via `selectinload` to
avoid N+1 (`repository.py:214`); transactional-rollback test isolation.

| Concept | Read |
|---|---|
| SQLAlchemy 2.0 async | **[core]** <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html> |
| Session basics — identity map, flush vs commit | **[core]** <https://docs.sqlalchemy.org/en/20/orm/session_basics.html> |
| Relationship loading and N+1 | **[core]** <https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html> |
| Connection pooling | **[deep]** <https://docs.sqlalchemy.org/en/20/core/pooling.html> |
| Alembic migrations | **[core]** <https://alembic.sqlalchemy.org/en/latest/tutorial.html> |
| Autogenerate and its limits | **[deep]** <https://alembic.sqlalchemy.org/en/latest/autogenerate.html> |
| asyncpg | **[ref]** <https://magicstack.github.io/asyncpg/current/> |
| Idempotency keys — the canonical write-up | **[core]** <https://brandur.org/idempotency-keys> |
| Idempotency as an API contract | **[core]** <https://docs.stripe.com/api/idempotent_requests> |

**Gate:** [`interview/persistence.md`](interview/persistence.md).

---

# Tier 4 — V0.4 · Planner and executor

**In the repo:** `orchestration/plan.py`, `state.py`, `executor.py`, `planner.py`,
`orchestrator.py`, `retry.py`.

**Algorithms — the densest milestone:**

| Algorithm | Where |
|---|---|
| Cycle detection (DFS colouring) on the proposed plan | `orchestration/plan.py:102` |
| Topological ordering | `plan.py` → `executor.py:119` |
| Ready-set / frontier scheduling, independent tasks concurrently | `executor.py:124-138` |
| Transitive skip propagation, iterated to a fixed point | `executor.py:147` |
| Explicit state-transition table; illegal transitions raise | `state.py:36-106` |
| Exponential backoff with **full jitter** | `retry.py:28` |

| Concept | Read |
|---|---|
| Topological sorting, Kahn and DFS | **[core]** <https://en.wikipedia.org/wiki/Topological_sorting> |
| `graphlib.TopologicalSorter` — the stdlib version, worth comparing | **[deep]** <https://docs.python.org/3/library/graphlib.html> |
| Backoff and jitter, with the graphs | **[core]** <https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/> |
| DAG orchestration in the wild | **[deep]** <https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html> |
| Durable execution — what AMOS approximates by hand | **[deep]** <https://docs.temporal.io/evaluate/understanding-temporal> |
| Decomposition prompting: plan-and-solve | **[core]** <https://arxiv.org/abs/2305.04091> |
| Least-to-most prompting | **[deep]** <https://arxiv.org/abs/2205.10625> |
| Chain-of-thought | **[deep]** <https://arxiv.org/abs/2201.11903> |
| Tree of Thoughts | **[ref]** <https://arxiv.org/abs/2305.10601> |
| Reflexion — the V0.7 critic's ancestor | **[deep]** <https://arxiv.org/abs/2303.11366> |

**Gate:** [`interview/orchestration.md`](interview/orchestration.md).

---

# Tier 5 — V0.5 · Retrieval

**In the repo:** `rag/chunking.py`, `embeddings.py`, `store.py`, `retrieval.py`, `ingest.py`,
`evaluation.py`, `cli.py`.

**Algorithms:**

| Algorithm | Where |
|---|---|
| Heading-aware split, then size-bounded windows with overlap and best-break search | `chunking.py:49,86,105,129` |
| Content hashing → re-ingest is a no-op | `ingest.py` |
| MRL truncation 3072 → 1536, then **mandatory L2 re-normalisation** | `embeddings.py:79-96,196` |
| Asymmetric embedding: different `task_type` for query vs document | `embeddings.py:138` |
| Quota-aware pacing that honours the provider's `retryDelay` | `embeddings.py:157` |
| Cosine distance `<=>`, converted to similarity | `store.py:110-123` |
| HNSW index (`vector_cosine_ops`) | `migrations/versions/5a881f4bdb98_*` |
| recall@k, strict recall, MRR | `evaluation.py:95` |

| Concept | Read |
|---|---|
| The original RAG paper | **[core]** <https://arxiv.org/abs/2005.11401> |
| Embeddings API and `task_type` | **[core]** <https://ai.google.dev/gemini-api/docs/embeddings> |
| Matryoshka representation learning — why truncation works at all | **[core]** <https://arxiv.org/abs/2205.13147> |
| pgvector: operators, index types, limits | **[core]** <https://github.com/pgvector/pgvector> |
| HNSW, the paper | **[deep]** <https://arxiv.org/abs/1603.09320> |
| HNSW, explained with pictures first | **[core]** <https://www.pinecone.io/learn/series/faiss/hnsw/> · <https://en.wikipedia.org/wiki/Hierarchical_navigable_small_world> |
| Tuning pgvector HNSW in practice | **[deep]** <https://supabase.com/blog/increase-performance-pgvector-hnsw> |
| Chunking strategies | **[core]** <https://www.pinecone.io/learn/chunking-strategies/> |
| Chunking, five levels, with code | **[deep]** <https://github.com/FullStackRetrieval-com/RetrievalTutorials> |
| IR metrics: MRR, NDCG | **[core]** <https://en.wikipedia.org/wiki/Mean_reciprocal_rank> · <https://en.wikipedia.org/wiki/Discounted_cumulative_gain> |
| Classical IR — the foundation under all of it | **[deep]** <https://nlp.stanford.edu/IR-book/> |
| Seven failure points of RAG — read before claiming RAG works | **[core]** <https://arxiv.org/abs/2401.05856> |
| RAG survey, for the map of what is missing here | **[deep]** <https://arxiv.org/abs/2312.10997> |
| Lost in the middle — position effects in long contexts | **[deep]** <https://arxiv.org/abs/2307.03172> |
| Contextual retrieval — a concrete improvement path | **[deep]** <https://www.anthropic.com/news/contextual-retrieval> |
| BM25 — the hybrid-search half AMOS does not have | **[deep]** <https://en.wikipedia.org/wiki/Okapi_BM25> |
| Embedding model comparison | **[ref]** <https://huggingface.co/blog/mteb> · <https://github.com/embeddings-benchmark/mteb> |
| Vector search vendor tradeoffs (ADR-001's counter-argument) | **[ref]** <https://www.timescale.com/blog/pgvector-vs-pinecone> · <https://ann-benchmarks.com/> |
| Vector search, plain-language explainer | **[ref]** <https://weaviate.io/blog/vector-search-explained> |

**Gate:** [`interview/rag.md`](interview/rag.md) — including the uncomfortable question about
widening ground truth after seeing results.

---

# Tier 6 — V0.6 · Memory (next milestone)

Pre-read **before** starting. The deliverable is `09-memory-architecture.md`, so the reading is
the work.

| Concept | Read |
|---|---|
| Memory tiers in an LLM agent — the paper that popularised the split | **[core]** <https://arxiv.org/abs/2310.08560> |
| Generative agents — episodic memory, reflection, retrieval by relevance/recency | **[core]** <https://arxiv.org/abs/2304.03442> |
| CoALA — a memory taxonomy to argue against | **[deep]** <https://arxiv.org/abs/2309.02427> |
| A production memory system's docs | **[ref]** <https://docs.letta.com/> |

The design question no paper answers for you: **which store does each memory kind belong in?**
Exact recall ("the user's name is X") is a relational lookup. Similarity search will occasionally
return someone else's fact. Write that down in the ADR.

---

# Tier 7 — V0.7 · Multi-agent

| Concept | Read |
|---|---|
| A real multi-agent system, with the costs stated | **[core]** <https://www.anthropic.com/engineering/multi-agent-research-system> |
| AutoGen — conversational multi-agent | **[deep]** <https://arxiv.org/abs/2308.08155> |
| Self-Refine — the critic loop | **[core]** <https://arxiv.org/abs/2303.17651> |
| Reflexion (again, now as implementation) | **[core]** <https://arxiv.org/abs/2303.11366> |
| Agent-to-agent protocol — structured messages, not chatter | **[deep]** <https://a2a-protocol.org/latest/> |

---

# Tier 8 — V0.8 · Asynchronous execution

The strongest distributed-systems content in the project. **[core] throughout.**

| Concept | Read |
|---|---|
| `SELECT … FOR UPDATE SKIP LOCKED` | <https://www.postgresql.org/docs/current/sql-select.html> · <https://www.postgresql.org/docs/current/explicit-locking.html> |
| Why `SKIP LOCKED` exists, in plain terms | <https://www.2ndquadrant.com/en/blog/what-is-select-skip-locked-for-in-postgresql-9-5/> |
| Postgres as a job queue, done carefully | <https://brandur.org/job-drain> |
| Exactly-once delivery is not available | <https://bravenewgeek.com/you-cannot-have-exactly-once-delivery/> |
| Why — the underlying impossibility | <https://en.wikipedia.org/wiki/Two_Generals%27_Problem> |
| Visibility timeouts, from the system that named them | <https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html> |
| Idempotent consumers | <https://microservices.io/patterns/communication-style/idempotent-consumer.html> |
| Transactional outbox | <https://microservices.io/patterns/data/transactional-outbox.html> |
| `LISTEN`/`NOTIFY` — polling's alternative | <https://www.postgresql.org/docs/current/sql-notify.html> |

---

# Tier 9 — V0.9 · Observability

| Concept | Read |
|---|---|
| OpenTelemetry Python | **[core]** <https://opentelemetry.io/docs/languages/python/> |
| Traces and spans | **[core]** <https://opentelemetry.io/docs/concepts/signals/traces/> |
| Sampling | **[core]** <https://opentelemetry.io/docs/concepts/sampling/> |
| GenAI semantic conventions — do not invent attribute names | **[core]** <https://opentelemetry.io/docs/specs/semconv/gen-ai/> |
| Dapper — the paper every tracer descends from | **[deep]** <https://research.google/pubs/dapper-a-large-scale-distributed-systems-tracing-infrastructure/> |
| The four golden signals | **[core]** <https://sre.google/sre-book/monitoring-distributed-systems/> |
| USE method | **[ref]** <https://www.brendangregg.com/usemethod.html> |
| SRE workbook — SLOs | **[ref]** <https://sre.google/workbook/table-of-contents/> |

---

# Tier 10 — V1.0 · Evaluation

| Concept | Read |
|---|---|
| How to actually build evals — read first, read twice | **[core]** <https://hamel.dev/blog/posts/evals/> |
| Task-specific evals and their design | **[core]** <https://eugeneyan.com/writing/evals/> |
| LLM patterns overview | **[deep]** <https://eugeneyan.com/writing/llm-patterns/> |
| LLM-as-judge: the paper, including agreement rates and biases | **[core]** <https://arxiv.org/abs/2306.05685> |
| G-Eval | **[deep]** <https://arxiv.org/abs/2303.16634> |
| RAG-specific metrics (faithfulness, the groundedness gap AMOS admits to) | **[core]** <https://docs.ragas.io/en/stable/> |
| Writing tests against a model | **[deep]** <https://docs.claude.com/en/docs/test-and-evaluate/develop-tests> |
| A harness to compare against before building one | **[ref]** <https://www.promptfoo.dev/docs/intro/> |

---

# Every algorithm in AMOS, in one table

The "what algorithms does it use" answer, with the file to open.

| # | Algorithm | Milestone | File |
|---|---|---|---|
| 1 | Bounded validate-and-repair over a non-deterministic producer | V0.1 | `agents/agent.py:85` |
| 2 | Request-id propagation through async context | V0.1 | `observability.py` |
| 3 | Bounded tool-calling loop (cap = termination proof) | V0.2 | `agents/tool_agent.py:90` |
| 4 | AST whitelist evaluation instead of `eval` | V0.2 | `tools/builtin/calculator.py:77` |
| 5 | Path canonicalisation then prefix containment check | V0.2 | `tools/builtin/read_file.py` |
| 6 | Host allowlist (SSRF containment) | V0.2 | `tools/builtin/http_get.py` |
| 7 | Schema validation of model-produced arguments pre-execution | V0.2 | `tools/base.py` |
| 8 | Idempotency-key deduplication | V0.3 | `api/persistence.py`, `database/repository.py` |
| 9 | Eager loading to avoid N+1 | V0.3 | `database/repository.py:214` |
| 10 | Transactional-rollback test isolation | V0.3 | `tests/integration/conftest.py` |
| 11 | DFS cycle detection on a proposed plan | V0.4 | `orchestration/plan.py:102` |
| 12 | Topological ordering | V0.4 | `orchestration/plan.py`, `executor.py:119` |
| 13 | Ready-set scheduling with concurrent independent tasks | V0.4 | `orchestration/executor.py:124` |
| 14 | Fixed-point transitive skip propagation | V0.4 | `orchestration/executor.py:147` |
| 15 | State machine with an explicit transition table | V0.4 | `orchestration/state.py:36` |
| 16 | Exponential backoff with full jitter | V0.4 | `orchestration/retry.py:28` |
| 17 | Heading-aware chunking, overlap, best-break search | V0.5 | `rag/chunking.py:49` |
| 18 | Content hashing for idempotent ingestion | V0.5 | `rag/ingest.py` |
| 19 | MRL truncation + L2 re-normalisation | V0.5 | `rag/embeddings.py:83,196` |
| 20 | Asymmetric query/document embedding | V0.5 | `rag/embeddings.py:138` |
| 21 | Rate-limit pacing honouring `retryDelay` | V0.5 | `rag/embeddings.py:157` |
| 22 | Cosine distance search, distance → similarity | V0.5 | `rag/store.py:110` |
| 23 | HNSW approximate nearest neighbour index | V0.5 | `migrations/versions/5a881f4bdb98_*` |
| 24 | recall@k, strict recall, MRR | V0.5 | `rag/evaluation.py:95` |
| — | *Not built yet* | | |
| 25 | Deterministic contradiction resolution | V0.6 | — |
| 26 | Task routing / classification accuracy | V0.7 | — |
| 27 | `SKIP LOCKED` claiming, visibility-timeout reclaim | V0.8 | — |
| 28 | Trace sampling | V0.9 | — |
| 29 | LLM-as-judge scoring, regression gating | V1.0 | — |
| 30 | Hybrid BM25 + vector, reciprocal rank fusion, reranking | future | — |

---

# Books

Four, in priority order. Everything above is free; these are the ones worth buying time for.

1. **Architecture Patterns with Python** — Percival & Gregory. *Free online.*
   <https://cosmicpython.com/> — repository, service layer, unit of work, ports and adapters,
   in exactly this stack. Read it end to end.
2. **Designing Data-Intensive Applications** — Kleppmann. <https://dataintensive.net/> —
   Chapters 5, 7, 9, 11 cover replication, transactions, consistency and streams. Read
   chapters 7 and 9 before V0.8.
3. **Site Reliability Engineering** — Google. *Free online.*
   <https://sre.google/sre-book/table-of-contents/> — read the chapters linked above; the rest
   is reference.
4. **Introduction to Information Retrieval** — Manning, Raghavan, Schütze. *Free online.*
   <https://nlp.stanford.edu/IR-book/> — chapters 1, 6, 8 for what a retrieval system is, before
   vectors existed.

---

# Suggested sequence

Rough, and deliberately not a schedule. Tier 0 and Tier A are the only ones that must come first.

| Block | Read | Then do |
|---|---|---|
| 1 | Tier 0 (0.1–0.4) | Read `llm/`, `agents/`, `api/` and explain the request path aloud |
| 2 | Tier 0 (0.5–0.7) + Tier 1 | Close `interview/foundation.md` |
| 3 | Tier 2 | Close `interview/agents.md` |
| 4 | Tier A (A.1–A.4) + Tier 3 | Close `interview/persistence.md` |
| 5 | Tier A (A.5–A.6) + Tier 4 | Close `interview/orchestration.md` |
| 6 | Tier 5 | Close `interview/rag.md` — **the advance gate to V0.6** |
| 7 | Tier 6 + Tier A.7 | Build V0.6 |
| 8+ | Tiers 7–10, at their milestones | Build V0.7–V1.0 |

---

# What to skip

Named explicitly, because a reading list without exclusions is just anxiety.

- **LangChain / LlamaIndex tutorials.** The orchestration layer *is* this project. Read
  LangGraph's concept page once for vocabulary; do not follow the tutorials.
- **Kubernetes, Kafka, microservices deployment.** ADR-004 rejected them. Reading them now
  builds nothing and risks claiming them.
- **Fine-tuning, training, GPU work.** No milestone touches it.
- **Prompt-engineering listicles.** Read the papers above instead; they say the same things with
  numbers.
- **Every arXiv PDF in full.** Abstract, figures, conclusion. Return only if a design decision
  depends on the detail.
