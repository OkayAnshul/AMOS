# Current State

> **Read this first when resuming.** It is the recovery mechanism — sufficient to restart cold
> after months away, without conversation history.

**Last updated:** 2026-09-05 (Session 5)

## Current Version
**V0.4 — Planner / Executor.** Shipped, tagged `v0.4`.

## Current Module
None in progress. Next is V0.5 (RAG) — the first milestone that uses pgvector.

## Completed Modules
- **Phase 0** — architecture, ADRs, domain model, data model, roadmap
- **V0.1** — provider protocol, structured output, bounded repair
- **V0.2** — tool registry, bounded tool loop, three safe tools
- **V0.3** — PostgreSQL persistence, execution trace, idempotency
- **V0.4** — planner, task DAG, state machine, retries, partial completion

## What Works
- `POST /v1/goals` → planner decomposes the goal → executor runs the DAG → synthesised answer
- Independent tasks run **concurrently**; dependents wait, then receive upstream results
- Task state machine where **illegal transitions raise** — 53 of them asserted
- Plans validated before persistence: unique ids, resolvable deps, **acyclic**, ≤10 tasks
- Bounded retries with exponential backoff + **full jitter**, budget per task
- Failure contained: dependents `SKIPPED` transitively, unrelated branches still run
- `PARTIALLY_COMPLETED` is a real outcome and is stored as the run's status
- `GET /v1/runs/{id}` → trace now includes the task DAG with states and attempt counts
- Everything from V0.1–V0.3 still works; `AMOS_PLANNING_ENABLED=false` reverts to the V0.2 agent
- 266 tests (18 need Postgres, skip cleanly without it); `mypy --strict` and `ruff` clean

**Verified live:** *"Work out 17% of 2340 and 23% of 1500, then tell me which is larger and by
how much"* → planner emitted a diamond (t1, t2 independent; t3 depends on both), all three used
the calculator, answer correct. 8 LLM calls, 4456 tokens, 7.5s. Trace persisted with dependencies
resolved to row UUIDs.

## What Does Not Work
Nothing broken. Not built: RAG (V0.5), memory (V0.6), multi-agent (V0.7), async workers (V0.8),
OTel (V0.9), evaluation (V1.0).

Known gaps, stated because an unlisted gap reads as a claim (`docs/17-failure-recovery.md`):
- **No re-planning** — a failed task is retried as written
- **No resumption** — a crash leaves an abandoned `RECEIVED` run; nothing sweeps it (V0.8)
- **Tasks are not idempotent** — safe only because every tool is read-only. This becomes a real
  bug the moment a write tool exists, which is why the registry refuses to register one
- pgvector installed, still unused

## ⚠️ Operating constraints
- **Gemini free tier: 20 requests/day, per model.** A 3-task plan costs **8 calls** — 40% of the
  day. `AMOS_LLM_MODEL` defaults to `gemini-3.5-flash-lite`; quota is per model.
  `gemini-2.5-flash` is 404.
- Develop against `FakeProvider`; spend live calls only on demos.
- `AMOS_PLANNING_ENABLED=false` for goals that need no decomposition.

## Current Architecture
```
Client → FastAPI → RunService → Orchestrator
                                   ├── Planner (LLM) → Plan → validate (acyclic)
                                   ├── Executor (NO LLM) → task DAG, state machine, retries
                                   │      └── ToolUsingAgent ⇄ ToolRegistry → tools
                                   └── Synthesis (LLM, skipped when 1 task or 0 succeeded)
                       ↓
                  PostgreSQL (runs, tasks, steps, llm_calls, tool_calls)
```

## Current Branch
`main` (V0.4 merged from `feat/v0.4-planner`)

## Last Known Good Commit
Tag `v0.4` — tests pass, app runs, demo verified.

## Known Bugs
None open. Eight fixed across V0.1–V0.4, all in `engineering/bugs-log.md`.

## Technical Debt
- `ToolUsingAgent._finalise` **still has never fired.** Delete it at V0.5 — carrying untested
  code is worse than removing it and restoring it if needed.
- No automated migration reversibility test; `downgrade base && upgrade head` is run by hand.
- No CI.
- Backups: a documented `pg_dump` command with **no restore drill**.
- `Step` rows are still one-per-run; V0.4 writes tasks but does not yet write a step per attempt.
  The schema supports it (`steps.task_id`); the repository does not use it.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Installed | fastapi 0.141.1, pydantic 2.13.5, google-genai 2.22.0, sqlalchemy 2.0.52, asyncpg 0.31, alembic 1.19.1 |
| Container runtime | **podman 5.8.2** + podman-compose (rootless). Docker still **not installed** — `compose.yaml` targets both but is **verified on podman only** |
| Database | PostgreSQL **18.6** + pgvector **0.8.6**, container `amos-postgres`, port 5432 |
| Migrations | single baseline `e25051359e64` (V0.3's was squashed — see `bugs-log.md`) |
| `.env` | working key + `AMOS_DATABASE_URL` (gitignored) |
| Git remote | SSH over **port 443** (port 22 blocked on this network) |
| GitHub | `OkayAnshul/AMOS`, public |

## How To Run
```bash
podman-compose up -d
.venv/bin/alembic upgrade head
.venv/bin/python -m amos

RUN=$(curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Work out 17% of 2340 and 23% of 1500, then say which is larger."}' | jq -r .run_id)
curl -s localhost:8000/v1/runs/$RUN | jq '.tasks, .tool_calls'
```

## How To Test
```bash
.venv/bin/python -m pytest                # 266; 248 pass + 18 skip without a database
.venv/bin/mypy src && .venv/bin/ruff check src tests
AMOS_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live   # opt-in, uses daily quota
```

## Exact Next Step

**Advance gate: unmet.** Four interview docs unread —
`docs/interview/{foundation,agents,persistence,orchestration}.md`. Per `CLAUDE.md`, V0.5 waits.

Outstanding from earlier sessions: verify `compose.yaml` on Docker and record the result in
`docs/18-deployment.md`.

If deferring again, **begin V0.5 — RAG**:

1. `git switch -c feat/v0.5-rag`
2. `CREATE EXTENSION vector;` in a migration; `chunks` and `documents` tables per
   `docs/05-data-model.md`.
3. **Embedding dimension is already decided: 1536, MRL-truncated and re-normalised** (ADR-008).
   pgvector's HNSW index caps `vector` at 2000 dims and `gemini-embedding-001` defaults to 3072.
   **Re-normalisation is mandatory** — truncating an L2-normalised vector leaves it
   un-normalised, and cosine distance then returns wrong rankings *silently*.
4. Build: `VectorStore` protocol → `PgVectorStore` → ingest pipeline (parse → chunk → embed →
   store, with a content hash for re-ingest detection) → retrieval as a **Tool**, so the agent
   chooses to retrieve → citations → refuse when retrieval is empty.
5. Build the HNSW index **after** loading the corpus, not before — it is markedly faster.
6. **Measure recall@k on a golden question set.** `docs/10-rag-architecture.md` must contain
   measured numbers, not defaults copied from a blog post. Without that number it is not RAG,
   it is a vector database with a claim attached.
7. Seed corpus: AMOS's own `docs/` — it makes the demo self-referential and the ground truth
   easy to check.

Full V0.5 spec and Definition of Done: `docs/19-roadmap.md`.
