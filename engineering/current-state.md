# Current State

> **Read this first when resuming.** Sufficient to restart cold after months away, without
> conversation history.

**Last updated:** 2026-09-09 (Session 7)

## Current Version
**V0.7 — Multi-Agent.** Shipped, tagged `v0.7`.

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · V0.5 RAG · V0.6 memory · **V0.7 multi-agent**

## What Works
- Goal → route to a specialist → tools/retrieval → critic review → cited answer, all persisted
- **Specialisation is structural**: disjoint tool allowlists enforced by construction, not prompts
- Critic has no tools; the revise loop is bounded; unresolved objections reach the user with
  confidence downgraded
- Facts survive a process restart; past runs findable by goal similarity
- **Routing measured: 10/10** (`python -m amos.agents.cli`)
- **Retrieval measured: recall@5 100%, MRR 0.958** (`python -m amos.rag.cli evaluate 5`)
- 389 tests; `mypy --strict` and `ruff` clean

## What Does Not Work
Not built: async workers (V0.8), OpenTelemetry (V0.9), evaluation harness (V1.0).

Gaps stated because an unlisted gap reads as a claim:
- **No agent-to-agent delegation** — the orchestrator assigns work
- **Routing measured on 10 self-authored cases** — cannot distinguish a good router from easy ones
- `remember_fact` persists a model decision with **no approval step and no provenance check**
- No forgetting/TTL; memories accumulate forever
- Tasks not idempotent (safe only because every tool is read-only)
- **No crash resumption** — an interrupted run leaves an abandoned `RECEIVED` row
- Still synchronous: no worker, no queue. **Not distributed task processing**

## ⚠️ Two different quotas
| | Limit | Does waiting help? |
|---|---|---|
| `generateContent` | **20 / day** per model | No |
| `embed_content` | **100 / minute**, counts *contents* | **Yes** |

A routed + reviewed answer costs ~4 calls. `AMOS_MULTI_AGENT_ENABLED=false`,
`AMOS_CRITIC_ENABLED=false` and `AMOS_PLANNING_ENABLED=false` each cut cost.

## Current Architecture
```
Client → FastAPI → RunService → Orchestrator (planner + executor)
                       │              └── AgentTeam
                       │                    ├── Router (LLM, validated, falls back)
                       │                    ├── Researcher  search·fetch·read·recall
                       │                    ├── Analyst     calculator
                       │                    └── Critic      NO tools, bounded revisions
                       └── episodic recording (after outcome; cannot fail the run)
                              ↓
   PostgreSQL: runs · tasks · steps · llm_calls · tool_calls · documents · chunks · memories
```

## Current Branch
`main` (V0.7 merged from `feat/v0.7-multi-agent`)

## Known Bugs
None open. Eighteen fixed across V0.1–V0.7, all in `engineering/bugs-log.md`.

## Technical Debt
- `steps` is still one row per run; the schema supports one per task attempt.
- No CI, no automated migration-reversibility check.
- `compose.yaml` **verified on podman only**; the Docker path is untested and labelled as such.
- Backups: a documented `pg_dump` command with **no restore drill**.
- `AgentTask` is defined but unused — no agent-to-agent delegation exists yet.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Container | podman 5.8.2 + podman-compose (rootless) |
| Database | PostgreSQL 18.6 + pgvector 0.8.6, container `amos-postgres` |
| Migrations | `e25051359e64` → `5a881f4bdb98` → `a0621f74b57c` |
| Corpus | 28 documents, 300 chunks |
| Version | defined in `src/amos/__init__.py`; the build reads it |
| GitHub | `OkayAnshul/AMOS`, public. Remote over **SSH port 443** (22 blocked here) |

## How To Run
```bash
podman-compose up -d && .venv/bin/alembic upgrade head
.venv/bin/python -m amos.rag.cli ingest docs     # ~4 min, paced for the quota
.venv/bin/python -m amos

curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"According to the docs, why was Celery rejected?"}' | jq
```

## How To Test
```bash
.venv/bin/python -m pytest                # 389; database tests skip if none is running
.venv/bin/mypy src && .venv/bin/ruff check src tests
.venv/bin/python -m amos.agents.cli       # routing accuracy (uses quota)
.venv/bin/python -m amos.rag.cli evaluate 5
```

## Exact Next Step

**Advance gate: unmet.** Seven interview docs unread. `docs/25-build-journal.md` is the narrative
version of the same material and is the better first read.

**Begin V0.8 — Asynchronous execution.** The strongest distributed-systems content in the project,
and the milestone that makes several current gaps closable.

1. `git switch -c feat/v0.8-async`
2. Worker process claiming tasks with `SELECT ... FOR UPDATE SKIP LOCKED` (ADR-003).
   The `tasks.claimed_at` column and the partial `idx_tasks_claimable` index **already exist** —
   added at V0.4 precisely so this milestone adds rows, not a migration.
3. `POST /v1/goals` returns **202 + run id**; poll `GET /v1/runs/{id}` for progress.
4. **Visibility timeout**: a task `RUNNING` past its limit becomes claimable again. That is what
   closes the "no crash resumption" gap.
5. **Idempotency becomes load-bearing here.** At-least-once delivery means a worker can complete a
   task and die before recording it. Today that is safe only because every tool is read-only —
   V0.8 should state that explicitly rather than inherit it silently.
6. Tests: concurrent workers claim **disjoint** tasks; a killed worker.s task is reclaimed; no
   task executes twice with visible effect; the visibility timeout is honoured.
7. Write `docs/12-event-system.md` — and note it will document a Postgres queue, **not** a broker.
   No broker is planned and none should be claimed.

Full V0.8 spec: `docs/19-roadmap.md`.
