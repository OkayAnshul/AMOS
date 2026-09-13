# Current State

> **Read this first when resuming.** Sufficient to restart cold after months away, without
> conversation history.

**Last updated:** 2026-09-13 (Session 13 — V1.3 delegation)

## Current Version
**V1.3 — Delegation.** The V0.1–V1.0 roadmap is complete; V1.1–V1.3 are past it.

Recent history: memory-reliability fixes (Session 9) · a **coherence audit** (Session 10) that
found six defects where the code disagreed with its own configuration and ~40 places where the
documentation disagreed with the code · **V1.1** (Session 11) — resumable reclaim, a dead-letter
queue, and trace context across the worker boundary. ADR-009 and ADR-010 added.

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · V0.5 RAG · V0.6 memory · V0.7 multi-agent · V0.8 async workers ·
V0.9 observability · V1.0 evaluation · V1.1 reliability · V1.2 evaluation credibility ·
**V1.3 delegation**

## What Works
```
goal → route to a specialist → plan a task DAG → tools + retrieval → critic review
     → cited answer, persisted, traced, and measurable
```
- Sync (`POST /v1/goals`) or async (`POST /v1/goals/async` → 202, worker executes).
  **The async route is mounted only when `AMOS_ASYNC_ENABLED=true`**
- A SIGKILLed worker's run is reclaimed by another and **resumes** — completed tasks are not
  re-run and the planner is not called again (V1.1, ADR-010)
- Runs the queue gives up on are `DEAD_LETTER`, readable at `GET /v1/runs/dead-letter`
- A queued run is **one trace end to end**, across both processes
- Facts persist across process restarts; past runs findable by goal similarity
- `GET /v1/runs/{id}` reconstructs any past run from stored rows
- OpenTelemetry spans; goal text excluded by default, metric labels allowlisted
- **587 tests** (589 collected, 2 live opt-in); `mypy --strict` and `ruff` clean; **CI runs
  with and without a database**, migrations both directions, and with no API key

### Measured
| What | Command | Result |
|---|---|---|
| End-to-end goals | `make eval` | **9/9** deterministic, refusal 2/2; groundedness 1.00 (judged, 1 failure excluded). 40852 tokens |
| Retrieval | `make retrieval` | recall@5 100%, recall@1 91.7%, MRR 0.958 — **measured on 12 questions; the set is now 16 and unre-run** |
| Routing | `make routing` | 10/10 — **measured on 10 cases; the set is now 15 and unre-run** |
| Memory storage | `make memory-trials` | store 100% (8/8), false claims 0% |

**All three sets are small and self-authored.** V1.2 made them larger (goals 6→9, retrieval
12→16, routing 10→15) and harder, and did **nothing** about independence — which is the limitation
that matters. A regression gate, not a characterisation of quality; `docs/16-evaluation.md`
carries that caveat next to every number.

`engineering/eval-baseline.json` stores the last scorecard, and `make eval` now fails on a
regression against it. Only deterministic rates gate; the judged score is context.

## What Does Not Work / Is Not Claimed
- **Not a distributed system** — multiple processes, one machine, one database
- **At-least-once delivery**, not exactly-once. V1.1 narrowed the duplicate-work window from a
  whole run to a single task; it did not close it — a worker dying between finishing a task and
  checkpointing it still redoes that task. No task-level idempotency keys
- **No re-planning.** A resumed run re-runs the stored plan faithfully, **including a bad one**
- **`remember_fact` persists a model decision** with no approval step and no provenance check on
  *content* (the source run is now recorded)
- No forgetting/TTL; memories accumulate forever
- Agents delegate within a task (V1.3), but **do not negotiate** — a delegate answers, it does
  not push back or ask clarifying questions. The orchestrator still assigns all top-level work
- No heartbeats, no graceful shutdown
- **No human calibration of the judge** — groundedness 1.00 has no known relationship to a
  human verdict. Adversarial coverage now exists, as tests (ADR-011), but the golden sets are
  **still self-authored**, which V1.2 did not fix
- **2 of 5 memory kinds built** — semantic and episodic. No working, conversation or
  knowledge-graph tier (`docs/09-memory-architecture.md` says why for each)
- No authentication, authorization or multi-user isolation. **Not safe to expose publicly**
- No UI. CLIs and FastAPI's `/docs`
- `Tool` has no output schema, no retry policy and no audit metadata (`docs/08`)
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
`feat/v1.3-delegation`. Merge to `main` when reviewed. Last known good commit on `main`: the
`v1.2` tag.

**Note on tags:** `v1.1` points at a commit whose `__version__` is still 1.0.0 — a slip recorded
in `bugs-log.md`. `v1.2` onward are correct, and `make release VERSION=x.y.z` does
bump→check→commit→tag in order so it cannot recur.

Tag shape changed with `make release`: `v0.1`–`v1.2` are two-part milestone tags, `v1.3.0` onward
are three-part, because the target derives the tag from `__version__` and that is necessarily
`major.minor.patch`. Not retagged — that would be the third tag rewrite in a day, for cosmetics.

## Known Bugs
None open. Twenty fixed across Phase 0–V1.0 and after, all in `engineering/bugs-log.md` with the lesson
each taught. The corrections table in `docs/25-build-journal.md` lists twenty-two things believed that
were false.

## Technical Debt
- `steps` is one row per run; the schema supports one per task attempt.
- `compose.yaml` **verified on podman only**; the Docker path is untested and labelled as such.
- Backups: a documented `pg_dump` with **no restore drill**.
- **Delegation quality is unmeasured.** The bounds are tested; whether models delegate *well*
  has no number, unlike routing accuracy.
- `steps` still one per run: V1.1 made *tasks* incremental, not steps.
- **`db_factory` tests commit for real and clean up at the end of the test body**, not in a
  fixture teardown — so a failing assertion leaks rows into the developer's database. Seen
  for real this session: a dead-lettered run from a failed test showed up in
  `GET /v1/runs/dead-letter`. The `db_session` tests do not have this problem, because they
  roll back.
- A resumed task reports `confidence: MEDIUM` because the stored row keeps the answer and not the
  confidence. Honest, but it means a resumed run's confidence is not the original's.
- Only the *latest* scorecard is stored, not per-case history — enough to catch a regression,
  not enough to see a trend.
- `MemoryReconciler` has **never been observed to fire** since the allowlist fix. Delete it if that
  is still true at the next milestone — the rule that removed `_finalise`.
- No `tests/contract/` or `tests/evaluation/` directory. The evaluation harness lives in `src/`
  and runs via `make eval`, never via pytest.
- **Two Phase-0 experiments were never run** and are now assumptions: recall at 1536 vs 3072
  dimensions (ADR-008's central claim, taken on the model card's word) and poll interval vs
  latency. Scored in `engineering/experiments-log.md`.
- The corpus figure is a snapshot: 300 chunks indexed, but `make ingest` would now index ~906,
  because `docs/interview/` and `docs/build-along/` were added after V0.5. Re-ingesting would
  invalidate every measured retrieval number, so re-measure and re-record together or not at all.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Container | podman 5.8.2 + podman-compose (rootless) |
| Database | PostgreSQL 18.6 + pgvector 0.8.6 |
| Migrations | `e25051359e64` → `5a881f4bdb98` → `a0621f74b57c` → `5892709841cc` → `453890cfd6a9` |
| Corpus | 28 documents, 300 chunks **indexed** (the 24 numbered docs + 4 interview docs as they stood at V0.5). A fresh `make ingest` would index 48 / ~906, and several indexed documents have been rewritten since |
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

**V1.4 — Authentication and multi-user isolation.** The largest change in the plan. A `users`
table, authentication on the API, and `user_id` on `runs`, `memories` and `documents`, with
scoping enforced **in the repository layer** so a query cannot forget it. One migration with a
backfill to a single owner.

The ADR must say out loud that `docs/01-requirements.md` currently lists multi-tenancy as an
explicit **non-goal**, and that `docs/13-security.md` has a table of controls this milestone
flips from ❌ to ✅. The whole "single user" framing across several documents changes with it.

**Worth re-running when convenient:** `make retrieval` and `make routing` still report numbers
measured on the older, smaller sets.

### Still outstanding: the advance gate

**Ten interview documents are unread** — `docs/interview/*.md`. This gate is the second of the
project's two equal objectives, and V1.1–V1.4 will add four more modules to it:

> A milestone is not complete until Anshul can answer that module's questions unaided.

Recommended order:
1. **`docs/25-build-journal.md`** — the narrative, thirteen chapters, how it was built and what was
   wrong. Read this first; it is the only document written to be read start to finish.
1b. **`docs/build-along/`** — if the answer is "build it myself". Twelve documents: what file to
   write, when, why now, and the trap waiting in each milestone. Contracts, not code — the git tags
   are the answer key.
2. `docs/24-study-plan.md` — what to study, tier by tier, with the algorithms at file:line.
3. `docs/interview/*.md` — ten documents, in milestone order.

`docs/19-roadmap.md` promised nothing beyond V1.0; V1.1–V1.4 are the scope chosen on
2026-09-13, and each still needs its ADR before any code.

**Unscheduled, and worth saying out loud:** the most valuable work left is arguably none of the
four — it is **having someone else author the golden sets**. Every quality number in this repo
rests on cases written by the person who built the system, and V1.2 enlarging them does not fix
that. Nor do these have homes: hybrid search and reranking · a per-run cost budget · re-planning
on failure · forgetting/TTL for memories · graceful worker shutdown · a restore drill for the
documented backup.
