# Current State

> **Read this first when resuming.** It is the recovery mechanism — sufficient to restart cold
> after months away, without conversation history.

**Last updated:** 2026-09-05 (Session 6)

## Current Version
**V0.5 — Retrieval (RAG).** Shipped, tagged `v0.5`.

## Current Module
None in progress. Next is V0.6 (Memory tiers).

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · **V0.5 RAG**

## What Works
- Ingestion: `python -m amos.rag.cli ingest docs` — parse → heading-aware chunk → embed → store
- `search_knowledge` tool: the agent retrieves when it judges the corpus relevant
- Answers cite sources; **empty retrieval produces an explicit refusal**, not a silent fallback
  to model memory
- Re-ingestion of unchanged content is a no-op (content hash)
- Ingestion commits **per document** and honours the provider's `retryDelay` on a 429
- `python -m amos.rag.cli evaluate [k]` — recall@k, strict recall, MRR
- Everything from V0.1–V0.4 still works
- 314 tests (18 need Postgres); `mypy --strict` and `ruff` clean

**Measured retrieval** — 28 docs, 300 chunks, 12-question golden set:

| k | recall (any valid source) | strict (primary only) | MRR |
|---|---|---|---|
| 1 | 91.7% | 50.0% | 0.917 |
| 3 | 100% | 83.3% | 0.958 |
| 5 | 100% | 91.7% | 0.958 |

Both figures are reported permanently because ground truth was widened *after* seeing results —
see `docs/10-rag-architecture.md`. Caveats: 12 questions is a small set, written by the same
person as the corpus.

**Verified live:** "why pgvector instead of Qdrant?" → cited `03-architecture-decisions.md`,
correct answer. "What does AMOS say about its Kubernetes autoscaling policy?" → correctly said
the documentation does not define one, rather than inventing it.

## What Does Not Work
Nothing broken. Not built: memory tiers (V0.6), multi-agent (V0.7), async workers (V0.8),
OTel (V0.9), evaluation harness for the whole system (V1.0).

Gaps stated because an unlisted gap reads as a claim:
- **No hybrid search, no reranking, no query rewriting.** Vector-only
- **No groundedness metric** — recall@k says the right passage was retrieved, not that the answer
  was faithful to it
- **No chunk-size sweep** — 1000/150 was reasoned, not compared
- Tasks still not idempotent (safe only because every tool is read-only)
- No resumption after a crash (V0.8)

## ⚠️ Operating constraints — two different quotas
| Thing | Quota | Waiting helps? |
|---|---|---|
| `generateContent` | **20 / day** per model | No — plan around it |
| `embed_content` | **100 / minute**, counts *contents* not requests | **Yes** — ingestion paces and retries |

`AMOS_LLM_MODEL` defaults to `gemini-3.5-flash-lite`. `gemini-2.5-flash` is 404.
`AMOS_PLANNING_ENABLED=false` skips planning for goals that do not need it.

## Current Architecture
```
Client → FastAPI → RunService → Orchestrator
                                   ├── Planner (LLM) → validated acyclic Plan
                                   ├── Executor (NO LLM) → DAG, state machine, retries
                                   │      └── ToolUsingAgent ⇄ ToolRegistry
                                   │            ├── calculator / read_file / http_get
                                   │            └── search_knowledge → pgvector
                                   └── Synthesis (LLM, skipped when 1 task or 0 succeeded)
                       ↓
        PostgreSQL: runs, tasks, steps, llm_calls, tool_calls, documents, chunks(vector 1536)
```

## Current Branch
`main` (V0.5 merged from `feat/v0.5-rag`)

## Last Known Good Commit
Tag `v0.5`.

## Known Bugs
None open. Nine fixed across V0.1–V0.5, all in `engineering/bugs-log.md`.

## Technical Debt
- `ToolUsingAgent._finalise` **still has never fired** across three milestones. Delete it at V0.6.
- `steps` is still one row per run; the schema supports one per task attempt and the repository
  does not use it.
- No automated migration-reversibility test (run by hand).
- No CI.
- Backups: a documented `pg_dump` command with **no restore drill**.
- `compose.yaml` still **verified on podman only** — Docker path untested and labelled as such.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Key deps | fastapi 0.141.1, pydantic 2.13.5, google-genai 2.22.0, sqlalchemy 2.0.52, asyncpg 0.31, alembic 1.19.1 |
| Container | podman 5.8.2 + podman-compose (rootless) |
| Database | PostgreSQL 18.6 + **pgvector 0.8.6 (in use)**, container `amos-postgres`, port 5432 |
| Migrations | `e25051359e64` → `5a881f4bdb98` (pgvector, documents, chunks) |
| Corpus | 28 documents, 300 chunks indexed |
| `.env` | key + `AMOS_DATABASE_URL` (gitignored) |
| Git remote | SSH over **port 443** (22 blocked on this network) |
| GitHub | `OkayAnshul/AMOS`, public |

## How To Run
```bash
podman-compose up -d && .venv/bin/alembic upgrade head
.venv/bin/python -m amos.rag.cli ingest docs      # ~4 min, paced for the quota
.venv/bin/python -m amos.rag.cli evaluate 5
.venv/bin/python -m amos

curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Search the AMOS docs and explain why pgvector was chosen over Qdrant."}' | jq
```

## How To Test
```bash
.venv/bin/python -m pytest                # 314; 296 pass + 18 skip without a database
.venv/bin/mypy src && .venv/bin/ruff check src tests
```

## Exact Next Step

**Advance gate: unmet.** Five interview docs unread —
`docs/interview/{foundation,agents,persistence,orchestration,rag}.md`.

Outstanding: verify `compose.yaml` on Docker; delete `_finalise` if still dead.

If deferring again, **begin V0.6 — Memory**:

1. `git switch -c feat/v0.6-memory`
2. The five memory kinds are already defined in `docs/04-domain-model.md`. **The milestone's real
   work is deciding which store each belongs in, and writing that down** —
   `docs/09-memory-architecture.md` is the deliverable, not the code.
3. Likely shape: *semantic* (durable facts, embedded — reuses `VectorStore`), *episodic* (past run
   outcomes in Postgres, retrieved by goal similarity), *working* (scoped to one run, in memory).
4. **Do not put everything in vectors.** Exact recall of "the user's name is X" is a relational
   lookup; similarity search is the wrong tool and will occasionally return someone else's fact.
5. Tests: fact recall across sessions; **contradicting facts resolved deterministically**;
   episodic retrieval surfaces relevant prior runs; working memory does not leak between runs.
6. Watch the quota: semantic memory writes cost embedding calls (100/min — fine); episodic
   retrieval costs one query embedding per run.

Full V0.6 spec and Definition of Done: `docs/19-roadmap.md`.
