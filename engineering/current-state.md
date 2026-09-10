# Current State

> **Read this first when resuming.** Sufficient to restart cold after months away, without
> conversation history.

**Last updated:** 2026-09-09 (Session 7)

## Current Version
**V1.0 — Evaluation.** Shipped, tagged `v1.0`. **The roadmap is complete.**

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · V0.5 RAG · V0.6 memory · V0.7 multi-agent · V0.8 async workers ·
V0.9 observability · **V1.0 evaluation**

## What Works
```
goal → route to a specialist → plan a task DAG → tools + retrieval → critic review
     → cited answer, persisted, traced, and measurable
```
- Sync (`POST /v1/goals`) or async (`POST /v1/goals/async` → 202, worker executes)
- A SIGKILLed worker's run is reclaimed and completed by another
- Facts persist across process restarts; past runs findable by goal similarity
- `GET /v1/runs/{id}` reconstructs any past run from stored rows
- OpenTelemetry spans; goal text excluded by default, metric labels allowlisted
- 480 tests; `mypy --strict` and `ruff` clean; **CI runs with and without a database**

### Measured
| What | Command | Result |
|---|---|---|
| End-to-end goals | `make eval` | 6/6 deterministic; groundedness 1.00 (judged) |
| Retrieval | `make retrieval` | recall@5 100%, recall@1 91.7%, MRR 0.958 |
| Routing | `make routing` | 10/10 |
| Memory storage | `make memory-trials` | store 100% (8/8), false claims 0% |

**All three sets are small and self-authored.** A regression gate, not a characterisation of
quality. `docs/16-evaluation.md` carries that caveat next to every number.

## What Does Not Work / Is Not Claimed
- **Not a distributed system** — multiple processes, one machine, one database
- **At-least-once delivery**, not exactly-once. Safety rests on tools being read-only;
  `remember_fact` could store a fact twice, which supersession makes harmless *by luck*
- **`remember_fact` persists a model decision** with no approval step and no provenance check on
  *content* (the source run is now recorded)
- **Trace context does not propagate into workers** — a queued run is a separate trace
- No forgetting/TTL; memories accumulate forever
- No agent-to-agent delegation; the orchestrator assigns work
- No dead-letter queue, no heartbeats, no graceful shutdown
- No adversarial evaluation; no human calibration of the judge
- Kubernetes, Kafka, Celery, microservices: **never built, never claimed**

## ⚠️ Four quota shapes, all measured
| | Limit |
|---|---|
| `generateContent`, `gemini-3.5-flash` | **20 / day** |
| `generateContent`, `gemini-3.5-flash-lite` | **15 / minute** |
| `embed_content` | **100 / minute**, counts *contents* not requests |
| `gemini-2.5-flash` | 404 — no longer served |

`AMOS_PLANNING_ENABLED`, `AMOS_MULTI_AGENT_ENABLED`, `AMOS_CRITIC_ENABLED` each cut cost.

## Current Branch
`main` (V1.0 merged from `feat/v1.0-evaluation`)

## Known Bugs
None open. Twenty-four fixed across Phase 0–V1.0 and after, all in `engineering/bugs-log.md` with the lesson
each taught. The corrections table in `docs/25-build-journal.md` lists twenty things believed that
were false.

## Technical Debt
- `steps` is one row per run; the schema supports one per task attempt.
- `compose.yaml` **verified on podman only**; the Docker path is untested and labelled as such.
- Backups: a documented `pg_dump` with **no restore drill**.
- `AgentTask` is defined but unused — no agent-to-agent delegation.
- No regression tracking over time; each eval run prints a number and nothing stores it.
- `MemoryReconciler` has **never been observed to fire** since the allowlist fix. Delete it if that
  is still true at the next milestone — the rule that removed `_finalise`.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Container | podman 5.8.2 + podman-compose (rootless) |
| Database | PostgreSQL 18.6 + pgvector 0.8.6 |
| Migrations | `e25051359e64` → `5a881f4bdb98` → `a0621f74b57c` → `5892709841cc` |
| Corpus | 28 documents, 300 chunks |
| Version | `src/amos/__init__.py`; the build reads it |
| GitHub | `OkayAnshul/AMOS`, public. Remote over **SSH port 443** (22 blocked here) |

## How To Run
```bash
make up && make migrate && make ingest    # ~4 min, paced for the quota
make run                                  # API
make worker                               # optional: async execution
```
`make help` lists everything.

## Exact Next Step

**The roadmap is complete. The remaining work is the advance gate, which has never been met.**

Ten interview documents are unread — `docs/interview/*.md`. That gate is the second of the
project's two equal objectives, and it is the only one still outstanding:

> A milestone is not complete until Anshul can answer that module's questions unaided.

Recommended order:
1. **`docs/25-build-journal.md`** — the narrative, eleven chapters, how it was built and what was
   wrong. Read this first; it is the only document written to be read start to finish.
2. `docs/24-study-plan.md` — what to study, tier by tier, with the algorithms at file:line.
3. `docs/interview/*.md` — ten documents, in milestone order.

**Beyond V1.0**, per `docs/19-roadmap.md`, nothing is promised. Candidates, each needing an ADR
before any code: agent-to-agent delegation · task-level idempotency keys (the honest fix for
at-least-once) · trace context propagation into workers · a dead-letter queue · authentication and
multi-user isolation · hybrid search and reranking · adversarial evaluation cases.

The most valuable next engineering work is arguably **none of those** — it is enlarging the golden
sets and having someone else write them, because every quality number in this repo currently rests
on cases authored by the person who built the system.
