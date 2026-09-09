# Current State

> **Read this first when resuming.** Sufficient to restart cold after months away, without
> conversation history.

**Last updated:** 2026-09-09 (Session 7)

## Current Version
**V0.9 — Observability.** Shipped, tagged `v0.9`.

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · V0.5 RAG · V0.6 memory · V0.7 multi-agent · V0.8 async workers · **V0.9 observability**

## What Works
- Goal → route to a specialist → tools/retrieval → critic review → cited answer, all persisted
- **Specialisation is structural**: disjoint tool allowlists enforced by construction, not prompts
- Critic has no tools; the revise loop is bounded; unresolved objections reach the user with
  confidence downgraded
- Facts survive a process restart; past runs findable by goal similarity
- **Routing measured: 10/10** (`python -m amos.agents.cli`)
- **Retrieval measured: recall@5 100%, MRR 0.958** (`python -m amos.rag.cli evaluate 5`)
- **Async submission**: `POST /v1/goals/async` → 202 in ~54ms, worker executes it
- **Crash recovery**: a SIGKILLed worker's run is reclaimed by another via visibility timeout
- **OpenTelemetry spans** for runs, LLM calls and tool calls; metrics for tokens and outcomes
- 441 tests; `mypy --strict` and `ruff` clean

## What Does Not Work
Not built: the evaluation harness (V1.0). That is the last milestone.

Gaps stated because an unlisted gap reads as a claim:
- **No agent-to-agent delegation** — the orchestrator assigns work
- **Routing measured on 10 self-authored cases** — cannot distinguish a good router from easy ones
- `remember_fact` persists a model decision with **no approval step and no provenance check**
- No forgetting/TTL; memories accumulate forever
- **Delivery is at-least-once**, not exactly-once. Safety rests on tools being read-only;
  `remember_fact` could store a fact twice, which supersession makes harmless *by luck*
- **Not a distributed system** — multiple processes, one machine, one database
- No dead-letter queue, no heartbeats, no graceful shutdown (SIGTERM relies on the timeout)
- **Trace context does not propagate into workers** — a queued run is a separate trace
- No sampling, no dashboards, no alerting — metrics are emitted and nothing consumes them

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
`main` (V0.9 merged from `feat/v0.9-observability`)

## Known Bugs
None open. Nineteen fixed across V0.1–V0.8, all in `engineering/bugs-log.md`.

## Technical Debt
- `steps` is still one row per run; the schema supports one per task attempt.
- No graceful worker shutdown — `SIGTERM` mid-run waits out the visibility timeout.
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
| Migrations | `e25051359e64` → `5a881f4bdb98` → `a0621f74b57c` → `5892709841cc` |
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
.venv/bin/python -m pytest                # 441; database tests skip if none is running
.venv/bin/mypy src && .venv/bin/ruff check src tests
.venv/bin/python -m amos.agents.cli       # routing accuracy (uses quota)
.venv/bin/python -m amos.rag.cli evaluate 5
```

## Exact Next Step

**Advance gate: unmet.** Nine interview docs unread. `docs/25-build-journal.md` is the narrative
version and the better first read.

**Begin V1.0 — Evaluation.** The final roadmap milestone, and the one that makes every quality
claim in the repo checkable by a command.

1. `git switch -c feat/v1.0-evaluation`
2. Two harnesses already exist and should be **unified, not duplicated**:
   `amos.rag.cli evaluate` (recall@k, MRR) and `amos.agents.cli` (routing accuracy).
3. Add a golden **goal** set — end-to-end, not component-level — scoring task completion, tool
   selection, and **groundedness** (is the answer supported by what was retrieved?). Groundedness
   is the one metric currently missing, and the one that would catch a confident wrong answer.
4. **LLM-as-judge is the obvious approach and needs stating honestly**: the judge is the same
   family of model being judged, so it shares failure modes. Any judged metric must be reported
   with that caveat, and preferably alongside a deterministic check.
5. CI regression gate — but note there is **no CI at all** yet, which is real debt. A gate with
   nothing running it is a script, not a gate.
6. Quota: an end-to-end eval run costs several calls per goal. Keep the golden set small and run
   it deliberately, not on every commit.
7. Write `docs/16-evaluation.md` with **measured numbers**, as `10-rag-architecture.md` was.

Full V1.0 spec: `docs/19-roadmap.md`.
