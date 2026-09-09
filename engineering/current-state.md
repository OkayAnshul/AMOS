# Current State

> **Read this first when resuming.** Sufficient to restart cold after months away, without
> conversation history.

**Last updated:** 2026-09-09 (Session 7)

## Current Version
**V0.6 — Memory.** Shipped, tagged `v0.6`.

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · V0.5 RAG · **V0.6 memory**

## What Works
- Goal → plan → DAG execution → tools → retrieval → cited answer, all persisted and traceable
- **Facts survive a process restart.** `remember_fact` / `recall_facts` / `recall_past_runs`
- Contradiction resolution: newest wins, superseded rows kept and inspectable
- Episodic recall: past runs found by goal similarity, current run excluded
- 7 tools registered when a database is present; 3 without one
- 350 tests; `mypy --strict` and `ruff` clean

**Verified live:** stated a preference in one process, killed it, recalled it from a new process.

## What Does Not Work
Not built: multi-agent (V0.7), async workers (V0.8), OTel (V0.9), evaluation harness (V1.0).

Gaps stated because an unlisted gap reads as a claim:
- **`remember_fact` persists a model decision with no approval step and no provenance check** —
  a prompt-injected instruction could write a false fact the system repeats later
- No forgetting, TTL or decay; memories accumulate forever
- No automatic extraction — facts stored only when the model calls the tool
- Conversation memory deliberately not built (no multi-turn API exists)
- Tasks still not idempotent (safe only because every tool is read-only)
- No resumption after a crash (V0.8)

## ⚠️ Two different quotas
| | Limit | Does waiting help? |
|---|---|---|
| `generateContent` | **20 / day** per model | No |
| `embed_content` | **100 / minute**, counts *contents* | **Yes** — ingest paces and retries |

`AMOS_LLM_MODEL` defaults to `gemini-3.5-flash-lite`. `AMOS_PLANNING_ENABLED=false` skips planning.

## Current Architecture
```
Client → FastAPI → RunService → Orchestrator
                       │           ├── Planner (LLM) → validated acyclic Plan
                       │           ├── Executor (NO LLM) → DAG, state machine, retries
                       │           │      └── ToolUsingAgent ⇄ ToolRegistry
                       │           │            calculator · read_file · http_get
                       │           │            search_knowledge · remember_fact
                       │           │            recall_facts · recall_past_runs
                       │           └── Synthesis (LLM, skipped when 1 task or 0 succeeded)
                       └── episodic recording (after the outcome; cannot fail the run)
                              ↓
   PostgreSQL: runs · tasks · steps · llm_calls · tool_calls · documents · chunks · memories
```

## Current Branch
`main` (V0.6 merged from `feat/v0.6-memory`)

## Known Bugs
None open. Fourteen fixed across V0.1–V0.6, all in `engineering/bugs-log.md`.

## Technical Debt
- `ToolUsingAgent._finalise` has **never fired across four milestones.** Delete it at V0.7.
- `steps` is still one row per run; the schema supports one per task attempt.
- No CI, no automated migration-reversibility check.
- `compose.yaml` **verified on podman only**; the Docker path is untested and labelled as such.
- Backups: a documented `pg_dump` command with **no restore drill**.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Container | podman 5.8.2 + podman-compose (rootless) |
| Database | PostgreSQL 18.6 + pgvector 0.8.6, container `amos-postgres`, port 5432 |
| Migrations | `e25051359e64` → `5a881f4bdb98` → `a0621f74b57c` |
| Corpus | 28 documents, 300 chunks |
| Git remote | SSH over **port 443** (22 blocked on this network) |
| GitHub | `OkayAnshul/AMOS`, public |

## How To Run
```bash
podman-compose up -d && .venv/bin/alembic upgrade head
.venv/bin/python -m amos.rag.cli ingest docs     # ~4 min, paced for the quota
.venv/bin/python -m amos

curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Remember that my preferred language is Python."}' | jq
```

## How To Test
```bash
.venv/bin/python -m pytest                # 350; database tests skip if none is running
.venv/bin/mypy src && .venv/bin/ruff check src tests
```

## Exact Next Step

**Advance gate: unmet.** Six interview docs unread —
`docs/interview/{foundation,agents,persistence,orchestration,rag,memory}.md`.
`docs/25-build-journal.md` is the narrative version of the same material.

**Begin V0.7 — Multi-agent.** This is the milestone that makes "multi-agent" an honest word; it is
not claimed anywhere before it.

1. `git switch -c feat/v0.7-multi-agent`
2. **Delete `_finalise` first** — dead for four milestones.
3. Agent registry + specialised agents. Each needs a *distinct tool allowlist*, not just a
   different prompt — three prompts over the same tools is not multi-agent, and an interviewer
   will ask exactly that.
   - **Researcher**: `search_knowledge`, `http_get`, `read_file`, `recall_facts`
   - **Analyst**: `calculator`, `recall_past_runs`
   - **Critic**: no tools; validates another agent.s output against its sources
4. **Structured A2A messages** (brief §10), never free-form text between agents.
5. The critic can force a retry — which means it must be bounded, or critic and producer can loop
   forever. That bound is the interesting engineering.
6. Router assigns tasks to agents; **measure routing accuracy** on a labelled task set, or
   "specialised agents" is an unmeasured claim.
7. Write `docs/07-agent-specification.md` (stub whose milestone arrives).
8. Quota: a multi-agent run costs more calls than any previous milestone. Develop against
   `FakeProvider`; spend live calls on one demo.

Full V0.7 spec: `docs/19-roadmap.md`.
