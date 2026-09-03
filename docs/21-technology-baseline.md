# 21 — Technology Baseline

Every technology AMOS uses or plans to use, with the version, why it was chosen, and its
official documentation. **Verified 2026-09-03** by checking sources — not recalled.

Three assumptions from memory turned out to be wrong when checked. They are listed at the
bottom, because the habit of verifying is the point of this document.

## Runtime

| Technology | Version | Purpose | Docs |
|---|---|---|---|
| Python | 3.14.4 | Only version on the dev machine | <https://docs.python.org/3/> |
| pip | 26.1 | Package installation | — |
| git | 2.54.0 | Version control | — |

## Core (V0.1)

| Technology | Version | Why | Docs |
|---|---|---|---|
| FastAPI | ≥0.119.1 | Async API, Pydantic-native, generates OpenAPI. **0.119.1 is the floor** — earlier versions warn/break on Python 3.14. | <https://fastapi.tiangolo.com/> |
| Pydantic | ≥2.12 | Validation at every boundary. **2.12 is the floor** — Python 3.14 support landed there. | <https://docs.pydantic.dev/latest/concepts/models/> |
| pydantic-settings | latest | 12-factor config from environment | <https://docs.pydantic.dev/latest/concepts/pydantic_settings/> |
| google-genai | ≥2.21.0 | Gemini SDK. Supports Python 3.14. | <https://googleapis.github.io/python-genai/> |
| pytest | latest | Testing | <https://docs.pytest.org/en/stable/how-to/fixtures.html> |
| ruff / mypy | latest | Lint and type check | — |

**Known limitation** — Free-tier Gemini is capped at a *daily per-model* request quota (20/day
for `gemini-3.5-flash`, measured), and its terms allow data to improve products. No confidential
input. Tests never call it (N-14) — with a 20/day budget, a test suite that hit the network
could exhaust the entire day's quota in one run.

## Model

| Model | Use | Free-tier quota (measured 2026-09-03) |
|---|---|---|
| `gemini-3.5-flash-lite` | **Default** for development | Own daily quota; fast (~1s/call) |
| `gemini-3.5-flash` | Demos, higher quality | **20 requests/day** |
| `gemini-2.5-flash` | ~~Fallback~~ | **404 NOT_FOUND** — no longer served |
| `gemini-embedding-001` | Embeddings, V0.5 | 2048 token input, 3072 dims default, MRL-truncatable, `task_type` |
| `gemini-embedding-2` | Alternative | 8192 token input, no `task_type` |

### The free tier is a DAILY per-model quota, not a rate limit

This is the single most operationally important fact in this document, and it is not what the
published guides say. The binding constraint measured from the API's own error:

```
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
model:   gemini-3.5-flash
limit:   20
```

**20 requests per day.** Not 15 per minute. A tool-using goal costs 2 LLM calls, so that is
roughly **10 goals per day per model**.

Two consequences that shaped the code:

1. **Quota is per model**, so `gemini-3.5-flash-lite` has its own separate allowance. It is the
   default (`AMOS_LLM_MODEL`) so development does not consume the quota reserved for demos.
2. **Every avoidable API call matters.** This is what made removing the redundant `_finalise`
   round trip a correctness-grade concern rather than an optimisation.

Model IDs change frequently. Check <https://ai.google.dev/gemini-api/docs/models> and
<https://ai.google.dev/gemini-api/docs/rate-limits> before assuming any of the above still hold.

## Persistence (V0.3)

| Technology | Version | Why | Docs |
|---|---|---|---|
| PostgreSQL | 18 | Relational + JSONB + vectors in one store | <https://www.postgresql.org/docs/current/> |
| pgvector | latest | Vector similarity (ADR-001) | <https://github.com/pgvector/pgvector> |
| SQLAlchemy | 2.0 async | ORM with real async support | <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html> |
| Alembic | latest | Migrations in version control | <https://alembic.sqlalchemy.org/en/latest/tutorial.html> |
| asyncpg | latest | Async Postgres driver | — |
| Docker Compose | not yet installed | Local orchestration | — |

**Known limitation** — pgvector HNSW/IVFFlat index **2000 dimensions maximum** for the `vector`
type (4000 for `halfvec`). This directly drives ADR-008.

## Later

| Technology | Milestone | Why |
|---|---|---|
| httpx | V0.2 | Async HTTP for the `http_get` tool |
| OpenTelemetry | V0.9 | Tracing — <https://opentelemetry.io/docs/languages/python/> |

## Deliberately not used

| Technology | Why not | Would reconsider if |
|---|---|---|
| Qdrant | Dual-write consistency cost with no scale benefit (ADR-001) | >5M vectors, or quantization needed |
| Celery + Redis | `SKIP LOCKED` solves it with zero new infrastructure (ADR-003) | Independent worker scaling, or scheduled tasks |
| Kafka / NATS | No event-streaming requirement exists | Multiple independent consumers of an event stream |
| Kubernetes | One process, one machine (ADR-004) | A real multi-service deployment |
| Neo4j | No graph query requirement | Relationship traversal becomes a core query pattern |
| LangChain / LlamaIndex | The orchestration layer *is* the project — delegating it removes the thing being learned | Never, for this project's purpose |

## Corrections found by verifying

Recorded because each would have been a real bug had it gone unchecked:

1. **`google-generativeai` is deprecated.** The current SDK is `google-genai`. Using the old
   package name would have meant building on a dead library.
2. **Gemini model IDs have moved to the 3.x line.** `gemini-3.5-flash` is current;
   memory suggested `gemini-1.5-*`, which would have failed at runtime.
3. **pgvector cannot index 3072 dimensions**, which is precisely `gemini-embedding-001`'s
   default output. Discovering this at V0.5 instead of Phase 0 would have meant re-embedding
   the entire corpus. See ADR-008.
4. **The free tier is 20 requests/day, not 15/minute** (V0.2). Every published summary said
   RPM; the API's own quota violation says `GenerateRequestsPerDayPerProjectPerModel`. AMOS's
   own error message repeated the wrong figure until the real 429 arrived.
5. **`gemini-2.5-flash` returns 404** and is no longer served, despite being listed as a
   fallback in this document at Phase 0.
6. **Tools and `response_schema` CAN be combined** (V0.2). The opposite was written into a
   docstring as "verified against the API" when it had not been tested. It cost one wasted API
   call per goal — a third of the daily budget.

**The rule:** check the current documentation before adopting or upgrading anything. Do not
trust recall for a version number, a model ID or an API signature.

**The stronger rule, learned at V0.2:** documentation is not the same as behaviour. Items 4-6
were all *wrong in the docs* or *never tested*, and only the live API revealed them. Where a
fact is load-bearing, probe the API and record what it returned. And never write "verified"
into a comment for something that was assumed.
