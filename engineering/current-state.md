# Current State

> **Read this first when resuming.** Sufficient to restart cold after months away, without
> conversation history.

**Last updated:** 2026-11-19 (Session 17 — V1.5, the demo track's foundation)

## Current Version
**V1.5 — Demo-ready foundation**, the first milestone of the demo track (V1.5–V2.0,
`docs/19-roadmap.md`). The V0.1–V1.0 roadmap is complete; V1.1–V1.4 are past it.

Recent history: memory-reliability fixes (Session 9) · a **coherence audit** (Session 10) that
found six defects where the code disagreed with its own configuration and ~40 places where the
documentation disagreed with the code · **V1.1** (Session 11) — resumable reclaim, a dead-letter
queue, and trace context across the worker boundary. ADR-009 and ADR-010 added.

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · V0.5 RAG · V0.6 memory · V0.7 multi-agent · V0.8 async workers ·
V0.9 observability · V1.0 evaluation · V1.1 reliability · V1.2 evaluation credibility ·
V1.3 delegation · V1.4 auth + isolation · **V1.5 demo-ready foundation**

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
- A 429 says whether its quota is **daily or per-minute** (`amos.llm.quota`); a daily embedding
  limit fails at once instead of sleeping through retries (V1.5)
- **`make backup` / `make restore-drill` / `make restore`** — the first drill passed 2026-11-19
- The corpus rebuilds in **one** free-tier day (850 chunks; `docs/build-along/` excluded), and an
  edited document **replaces** its old version on re-ingest
- **638 tests** (640 collected, 2 live opt-in), in their own database `amos_test`; `mypy --strict`
  and `ruff` clean; **CI runs with and without a database**, migrations both directions, and with
  no API key

### Measured
| What | Command | Result |
|---|---|---|
| End-to-end goals | `make eval` | **9/9** deterministic, refusal 2/2; groundedness 0.94. 70905 tokens. 2026-11-19, 850 chunks. A first run that day lost 2 goals to Gemini `503` and was not stored |
| Retrieval | `make retrieval` | **recall@5 100% (16/16), recall@1 62.5%, MRR 0.771** — 2026-11-19, 850 chunks. Not one series with V0.5's 0.958. Most rank-1 misses are unlabelled correct answers; one question is false since V1.4. Labels not widened |
| Routing | `make routing` | **13/15 (86.7%)**, 2026-09-14. Both misses are lookup-then-compute cases labelled `analyst` and routed to `researcher` — which V1.3 delegation may have made the better answer. Labels deliberately not changed |
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
- **Authentication yes, authorization no.** Any authenticated user can call every endpoint —
  no scopes, roles or read-only keys. The gap most likely to be assumed solved
- No key rotation, no audit of auth attempts, no TLS, no inbound rate limiting.
  **Still not safe to expose publicly**
- **Without a database the API is unauthenticated** — by design, with a startup warning
- The corpus is shared across users by design (`documents.user_id` NULL = system corpus)
- No UI. CLIs and FastAPI's `/docs`
- `Tool` has no output schema, no retry policy and no audit metadata (`docs/08`)
- Kubernetes, Kafka, Celery, microservices: **never built, never claimed**

## ⚠️ Five quota shapes, all measured (per-model free-tier numbers are no longer on the public docs page — read them off the account's AI Studio page)
| | Limit |
|---|---|
| `generateContent`, `gemini-3.5-flash` | **20 / day** |
| `generateContent`, `gemini-3.5-flash-lite` | **15 / minute** |
| `embed_content` | **100 / minute**, counts *contents* not requests |
| `embed_content` | **1000 / day** — hit 2026-09-14 by a corpus rebuild. Named *requests*; almost certainly counts contents. Since V1.5 it fails fast. Resets midnight Pacific |
| `gemini-2.5-flash` | 404 — no longer served |

`AMOS_PLANNING_ENABLED`, `AMOS_MULTI_AGENT_ENABLED`, `AMOS_CRITIC_ENABLED` each cut cost.

## Current Branch
`main`, at `v1.5.0`. No open feature branch. The next one is `feat/v1.6-provider`.

**Note on tags:** `v1.1` points at a commit whose `__version__` is still 1.0.0 — a slip recorded
in `bugs-log.md`. `v1.2` onward are correct, and `make release VERSION=x.y.z` does
bump→check→commit→tag in order so it cannot recur.

Tag shape changed with `make release`: `v0.1`–`v1.2` are two-part milestone tags, `v1.3.0` onward
are three-part, because the target derives the tag from `__version__` and that is necessarily
`major.minor.patch`. Not retagged — that would be the third tag rewrite in a day, for cosmetics.

## Known Bugs
**None open.** The pgvector similarity failure was reproduced and explained at V1.5 — the planner
choosing between an exact scan and the HNSW index — and the test fixed (`bugs-log.md`,
`experiments-log.md`, 2026-11-19). Twenty-three fixed in all, each in `engineering/bugs-log.md` with
the lesson it taught.

## Technical Debt
- `steps` is one row per run; the schema supports one per task attempt.
- `compose.yaml` **verified on podman only**; the Docker path is untested and labelled as such.
- **Backups are manual** (V1.5): `make backup` and a passing drill, but no schedule, no retention,
  no off-machine copy, and the drill has only restored a fresh dump into the same schema.
- **Delegation quality is unmeasured.** The bounds are tested; whether models delegate *well*
  has no number, unlike routing accuracy.
- `steps` still one per run: V1.1 made *tasks* incremental, not steps.
- **A transient provider error ends the run.** A Gemini `503` raised outside the task executor
  (router, planner) is never retried — it cost two eval goals on 2026-11-19. Scheduled in V1.6 as a
  provider wrapper both providers share.
- **`db_factory` tests commit for real and clean up at the end of the test body**, not in a
  fixture teardown — so a failing assertion leaks rows. Since V1.5 into `amos_test`, not the demo's
  database. Seen
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
- **The retrieval golden set's labels are older than the corpus.** Documents that answer its
  questions now exist unlabelled, and "Why is there no authentication yet?" is false since V1.4.
  Fixing it by relabelling after seeing results is the thing the set's docstring forbids; the real
  fix is an independently authored set.
- **The corpus lags `docs/` by this session's edits.** Edited documents re-embed on the next
  `make ingest` (they now replace their old versions); today's embedding quota was mostly spent on
  the rebuild, so run it tomorrow, then `make backup`.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Container | podman 5.8.2 + podman-compose (rootless) |
| Database | PostgreSQL 18.6 + pgvector 0.8.6 |
| Migrations | `e25051359e64` → `5a881f4bdb98` → `a0621f74b57c` → `5892709841cc` → `453890cfd6a9` → `835121ee2bd2` |
| Users | `alembic upgrade head` creates `default` and **prints its key once**. `AMOS_BOOTSTRAP_API_KEY` sets it instead |
| Corpus | **41 docs / 850 chunks**, rebuilt 2026-11-19 in one run; backed up (`backups/amos-20261119-140511.dump`, 6.6 MB, drill passed). Before that: EMPTY, destroyed 2026-09-13. A rebuild on 2026-09-14 hit the daily embedding quota partway and — running pre-fix code in one transaction — rolled back everything. Now 51 docs / ~1070 chunks, likely more than one free-tier day. Since the per-document-commit fix, `make ingest` keeps what it finishes and resumes: **run it on two consecutive days** |
| Test database | `amos_test`, created and migrated by the test suite on first use (`AMOS_TEST_DATABASE_URL` overrides) |
| Version | `src/amos/__init__.py`; the build reads it |
| GitHub | `OkayAnshul/AMOS`, public. Remote over **SSH port 443** (22 blocked here) |
| Coding sessions | Since 2026-11-17 the terminal account switcher set the assistant CLI's subprocess-environment scrub (`…_SUBPROCESS_ENV_SCRUB=1`), which on Linux also puts every session command in a PID/user namespace with nested namespaces disabled — **podman cannot start inside a session**. Run `make up` in your own terminal. A config-folder switcher that needs no scrub was written 2026-11-19 (the Studio project's account-switcher folder (`config-dirs/`)), not yet installed |
| Attribution | The assistant's instruction file is untracked and imports `docs/working-agreement.md`. `make attribution-check` before every push |

## How To Run
```bash
make up && make migrate && make ingest    # ~10 min, paced; fits one free-tier day
make backup                               # after every ingest
make run                                  # API
make worker                               # optional: async execution
```
`make help` lists everything.

## Exact Next Step

**V1.6 — a second LLM provider** (`docs/19-roadmap.md`), started with
`engineering/next-session-prompt.md`. It now includes the transient-error retry found at V1.5.

Before it (Anshul):
1. A free Groq API key in `.env` as `GROQ_API_KEY`.
2. Read the Gemini free-tier numbers for `gemini-embedding-001` and `gemini-3.5-flash` off the AI
   Studio rate-limit page — the public docs page stopped listing them on or before 2026-11-19 — and
   paste them at the start of the session.
3. `make ingest` (re-embeds this session's edited docs, replacing old versions), then `make backup`.

### The advance gate — thirteen documents, never met

`docs/interview/*.md`, now including `operations.md` (V1.5). This is the second of the project's
two equal objectives, and the teacher demo is exactly its test: explaining the system unaided.
Order: `docs/25-build-journal.md` → `docs/24-study-plan.md` → `docs/interview/*.md` in milestone
order. Building further before closing it is a deliberate choice against the working agreement's
own rule, made on 2026-09-13 and still worth revisiting.

**Unscheduled, and worth saying out loud:** the most valuable work left is arguably none of the
demo track — it is **having someone else author the golden sets**. V1.5's retrieval re-measure is
the clearest evidence yet: most of its misses are labels that aged, not retrieval that failed.
Authorization stays out of the demo track (localhost, one presenter) and remains the strongest
candidate after V2.0.
