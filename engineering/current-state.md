# Current State

> **Read this first when resuming.** Sufficient to restart cold after months away, without
> conversation history.

**Last updated:** 2026-11-21 (Session 18 — V1.6 reliability and isolation, V1.7 live run record; the track re-planned)

## Current Version
**V1.7 — Live run record and read API**, the third milestone of the demo track (V1.5–V2.0,
`docs/19-roadmap.md`, **re-planned 2026-11-20**: V1.8 web page → V1.9 web research → V1.10 complex
goals → V2.0 demo). The V0.1–V1.0 roadmap is complete.

Recent history: memory-reliability fixes (Session 9) · a **coherence audit** (Session 10) that
found six defects where the code disagreed with its own configuration and ~40 places where the
documentation disagreed with the code · **V1.1** (Session 11) — resumable reclaim, a dead-letter
queue, and trace context across the worker boundary. ADR-009 and ADR-010 added.

## Completed Modules
Phase 0 · V0.1 provider + structured output · V0.2 tools · V0.3 persistence + trace ·
V0.4 planner/executor · V0.5 RAG · V0.6 memory · V0.7 multi-agent · V0.8 async workers ·
V0.9 observability · V1.0 evaluation · V1.1 reliability · V1.2 evaluation credibility ·
V1.3 delegation · V1.4 auth + isolation · V1.5 demo-ready foundation · V1.6 reliability and isolation · **V1.7 live run record and read API**

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
- A provider 5xx is `ProviderUnavailableError` (HTTP 503), **retried once inside the caller's
  deadline** wherever it happens — planner and synthesis included (V1.6, ADR-014). The executor no
  longer retries a daily 429 or a rejected key
- Dead letters, episodic recall and retrieval are **owner-scoped** (V1.6 — three leaks closed); a
  worker lost on a run's final attempt dead-letters it
- **A run is recorded as it happens** (V1.7, ADR-015): every model and tool call — each real
  attempt — written as it finishes, tagged with task, agent and purpose; routing decisions, critic
  verdicts, task starts and retries as events; one sequence orders all of it. `GET /v1/runs/{id}`
  is live while the run runs. Totals are summed from stored calls; delegates are counted
- **Per-run options** — planning, multi-agent, critic, delegation, memory — switch *off* what the
  server enabled, stored on the run and applied by the worker (ADR-016)
- **Read API:** runs list, memories and history, corpus, chunks, uploads (user-owned `.md`/`.txt`),
  system (provider, quota meter, features, bounds, queue, agents, tools), insights — every
  personal list owner-scoped with an isolation test
- **`make backup` / `make restore-drill` / `make restore`** — the first drill passed 2026-11-19
- The corpus rebuilds in **one** free-tier day (850 chunks; `docs/build-along/` excluded), and an
  edited document **replaces** its old version on re-ingest
- **698 tests** (700 collected, 2 live opt-in), in their own database `amos_test`; `mypy --strict`
  and `ruff` clean; **CI runs with and without a database**, migrations both directions, and with
  no API key

### Measured
| What | Command | Result |
|---|---|---|
| End-to-end goals | `make eval` | **9/9** deterministic, refusal 2/2; groundedness 1.00. 65 320 tokens. **2026-11-21, 880 chunks** — the stored baseline. Two runs the day before under Gemini overload: 7/8 + 1 unmeasurable, then 9/9 with 20 provider retries (≥16 cleared, `experiments-log.md`) |
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
- No UI yet (V1.8). CLIs, FastAPI's `/docs`, and since V1.7 a read API built for the page
- `Tool` has no output schema, no retry policy and no audit metadata (`docs/08`)
- Kubernetes, Kafka, Celery, microservices: **never built, never claimed**

## ⚠️ Quota shapes — from the account's AI Studio page, 2026-11-19 (`docs/21-technology-baseline.md`)
| | Limit |
|---|---|
| `gemini-3.5-flash-lite` (the default) | **15 / minute**, **500 / day**, 250K tokens/min. The eval suite peaked at 16/min |
| `gemini-embedding-001` | **100 contents / minute**, **30K tokens / minute** (new), **1000 / day**. 903 used on 2026-11-19 |
| `gemini-3.5-flash` | **unknown** — absent from the page, still in `models.list`. Was 20/day |
| `gemma-4-31b-it`, `gemma-4-26b-a4b-it` | 30 / minute, 16K tokens/min, **14.4K / day** — untried with tools |
| Search grounding (Gemini 2 / 2.5) | 1.5K / day; **0 for Gemini 3+** |
| Groq `openai/gpt-oss-120b` (not yet used) | 30 / minute, 1K / day, but **8K tokens / minute, 200K / day** — ≈ 1 goal a minute, ≈ 25 a day at V1.5's 7.9K tokens per goal |

Daily limits reset at midnight Pacific. Since V1.5 a daily 429 fails fast.

`AMOS_PLANNING_ENABLED`, `AMOS_MULTI_AGENT_ENABLED`, `AMOS_CRITIC_ENABLED` each cut cost.

## Current Branch
`main`, at `v1.7.0`. No open feature branch. The next one is `feat/v1.8-web-page`.

**Note on tags:** `v1.1` points at a commit whose `__version__` is still 1.0.0 — a slip recorded
in `bugs-log.md`. `v1.2` onward are correct, and `make release VERSION=x.y.z` does
bump→check→commit→tag in order so it cannot recur.

Tag shape changed with `make release`: `v0.1`–`v1.2` are two-part milestone tags, `v1.3.0` onward
are three-part, because the target derives the tag from `__version__` and that is necessarily
`major.minor.patch`. Not retagged — that would be the third tag rewrite in a day, for cosmetics.

## Known Bugs
**None open.** The pgvector similarity failure was reproduced and explained at V1.5 — the planner
choosing between an exact scan and the HNSW index — and the test fixed (`bugs-log.md`,
`experiments-log.md`, 2026-11-19). V1.6 fixed seven more found by surveying the read surface —
three isolation leaks, a run stuck in `QUEUED` forever, an unclassified 503, retried daily 429s, and
an iteration setting that did nothing. Thirty fixed in all, each in `engineering/bugs-log.md` with
the lesson it taught.

V1.7 closed the record's gaps (unordered, unattributed calls; no verdicts; discarded delegate
costs; failed runs with no calls; worker without episodes or metrics) and a duplicated `RunStatus`.

## Technical Debt
- **SQLAlchemy is capped below 2.1.** 2.1 changed `Select`'s typing (four errors in
  `database/repository.py`); the upgrade is unrun and the database tests have never run on it.
  Other dependencies are still floor-only, so CI can drift from `.venv` again — check the last CI run
  at session start (`gh run list`).
- `steps` is one row per run; the schema supports one per task attempt.
- `compose.yaml` **verified on podman only**; the Docker path is untested and labelled as such.
- **Backups are manual** (V1.5): `make backup` and a passing drill, but no schedule, no retention,
  no off-machine copy, and the drill has only restored a fresh dump into the same schema.
- **Delegation quality is unmeasured.** The bounds are tested; whether models delegate *well*
  has no number, unlike routing accuracy.
- `steps` still one per run: V1.1 made *tasks* incremental, not steps.
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
- **The corpus lags `docs/` by each session's edits** until the next `make ingest` (edited documents
  replace their old versions). Run it, then `make backup`, at the start of a session.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Container | podman 5.8.2 + podman-compose (rootless) |
| Database | PostgreSQL 18.6 + pgvector 0.8.6 |
| Migrations | `e25051359e64` → `5a881f4bdb98` → `a0621f74b57c` → `5892709841cc` → `453890cfd6a9` → `835121ee2bd2` → `9c1f0d7e2a14` (V1.7 run record) |
| Users | `alembic upgrade head` creates `default` and **prints its key once**. `AMOS_BOOTSTRAP_API_KEY` sets it instead. A `presenter` user was created 2026-11-20 for live checks and the demo; its key is not in the repo |
| Corpus | **42 docs / 880 chunks**, re-ingested 2026-11-20 (7 edited docs replaced their old versions); backed up (`backups/amos-20261120-144852.dump`, 6.8 MB). Rebuilt from empty on 2026-11-19 in one run (850 chunks; drill passed). A full rebuild fits one free-tier day since V1.5 |
| Test database | `amos_test`, created and migrated by the test suite on first use (`AMOS_TEST_DATABASE_URL` overrides) |
| Version | `src/amos/__init__.py`; the build reads it |
| GitHub | `OkayAnshul/AMOS`, public. Remote over **SSH port 443** (22 blocked here) |
| Coding sessions | Podman works inside a session again (verified 2026-11-20: `podman ps`, `make backup`). From 2026-11-17 to 11-19 an account switcher's environment scrub blocked containers in sessions; if that returns, run `make up` in your own terminal |
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

**V1.8 — mission control web page** (`docs/19-roadmap.md`). Branch `feat/v1.8-web-page`. ADR-017
(plain HTML/CSS/JS, no build step, no CDN, self-hosted fonts, strict CSP) before code. The read API
it draws on is complete; the design is in the approved plan: a live lane graph — one lane per agent,
a capsule per task, a tick per call — with an inspector for every request and response.

Browser checks use Playwright for Python as a dev dependency driving the installed Chrome
(`/usr/sbin/google-chrome-stable`), no browser download.

### The advance gate — fifteen documents, never met

`docs/interview/*.md` — fifteen: V1.6's questions added to `reliability.md` and `security.md`, and `run-record.md` (V1.7). This is the second of the project's
two equal objectives, and the teacher demo is exactly its test: explaining the system unaided.
Order: `docs/25-build-journal.md` → `docs/24-study-plan.md` → `docs/interview/*.md` in milestone
order. Building further before closing it is a deliberate choice against the working agreement's
own rule, made on 2026-09-13 and still worth revisiting.

**Unscheduled, and worth saying out loud:** the most valuable work left is arguably none of the
demo track — it is **having someone else author the golden sets**. V1.5's retrieval re-measure is
the clearest evidence yet: most of its misses are labels that aged, not retrieval that failed.
Authorization stays out of the demo track (localhost, one presenter) and remains the strongest
candidate after V2.0.
