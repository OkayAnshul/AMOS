# Current State

> **Read this first when resuming.** It is the recovery mechanism — sufficient to restart cold
> after months away, without conversation history.

**Last updated:** 2026-09-03 (Session 4)

## Current Version
**V0.3 — Persistence and Trace.** Shipped, tagged `v0.3`. The hinge milestone.

## Current Module
None in progress. Next is V0.4 (Planner / Executor).

## Completed Modules
- **Phase 0** — architecture, ADRs, domain model, data model, roadmap
- **V0.1** — provider protocol, structured output, bounded repair
- **V0.2** — tool registry, bounded tool loop, three safe tools
- **V0.3** — PostgreSQL persistence, full execution trace, idempotency

## What Works
- `POST /v1/goals` → runs the goal, persists everything, returns `run_id`
- `GET /v1/runs/{id}` → the complete trace **from stored rows alone**: steps, every LLM call
  (provider, model, tokens, latency, repair attempt), every tool call (name, arguments, status,
  output, latency)
- `idempotency-key` header → a resubmit returns the original run without re-running the agent
- Failed runs keep their partial trace, including tokens already spent
- Run row written **before** execution, so a crash still leaves evidence
- **Runs fine with no database**: `/v1/runs/{id}` returns 503, everything else works
- 136 tests (18 need Postgres and skip cleanly without it); `mypy --strict` and `ruff` clean

**Verified live:** goal → `run_id` → full trace fetched from the DB; idempotent resubmit
returned the same run; migration up/down round trip.

## What Does Not Work
Nothing broken. Not built: planner (V0.4), RAG (V0.5), memory (V0.6), multi-agent (V0.7),
async workers (V0.8), OTel (V0.9), evaluation (V1.0).

pgvector is **installed but unused** until V0.5.

## ⚠️ Operating constraints
- **Gemini free tier: 20 requests/day, per model.** Not a rate limit. A tool-using goal costs
  2 calls. `AMOS_LLM_MODEL` defaults to `gemini-3.5-flash-lite` because quota is per model.
  `gemini-2.5-flash` is 404 / no longer served.
- Tests never touch the network (N-14) — one run could exhaust a day's quota.

## Current Architecture
```
Client → FastAPI → RunService → ToolUsingAgent ⇄ ToolRegistry → tools
                       ↓                ↓
                  PostgreSQL       LLMProvider → Gemini
                  (runs, steps, llm_calls, tool_calls)
```

## Current Branch
`main` (V0.3 merged from `feat/v0.3-persistence`)

## Last Known Good Commit
Tag `v0.3` — tests pass, app runs, demo verified.

## Known Bugs
None open. Seven fixed across V0.1–V0.3, all in `engineering/bugs-log.md`.

## Technical Debt
- `ToolUsingAgent._finalise` still has not fired in practice. Delete it at V0.4 if it stays dead.
- `Step` has exactly one row per run until V0.4 gives it a reason to have more.
- Backups are a documented `pg_dump` command with **no restore drill** — not a strategy.
- No CI. Tests run only when someone runs them.
- `RunService.execute` takes `agent: object` — it predates a shared agent protocol. If V0.7 adds
  a third agent type, define one.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4, `.venv/` |
| Installed | fastapi 0.141.1, pydantic 2.13.5, google-genai 2.22.0, sqlalchemy 2.0.52, asyncpg 0.31, alembic 1.19.1 |
| Container runtime | **podman 5.8.2** + podman-compose (rootless). Docker not installed — `compose.yaml` is written to work with both but has **only been verified on podman** |
| Database | PostgreSQL **18.6** + pgvector **0.8.6**, container `amos-postgres`, port 5432 |
| `.env` | working key + `AMOS_DATABASE_URL` (gitignored) |
| Git remote | SSH over **port 443** (port 22 blocked on this network) |
| GitHub | `OkayAnshul/AMOS`, public |

## How To Run
```bash
podman-compose up -d                     # or: docker compose up -d
.venv/bin/alembic upgrade head
.venv/bin/python -m amos                 # http://127.0.0.1:8000

RUN=$(curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"What is 12% of 500?"}' | jq -r .run_id)
curl -s localhost:8000/v1/runs/$RUN | jq   # the whole story of that request
```

## How To Test
```bash
.venv/bin/python -m pytest                # 136 with a DB; 118 pass + 18 skip without one
.venv/bin/mypy src && .venv/bin/ruff check src tests
AMOS_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live   # opt-in, uses daily quota
```

## Exact Next Step

**Advance gate: unmet.** Three interview docs now unread —
`docs/interview/{foundation,agents,persistence}.md`. Per `CLAUDE.md`, V0.4 waits on them.

Also outstanding from the V0.3 decision: **verify `compose.yaml` on Docker** and note the result
in `docs/18-deployment.md`. Until then the doc's Docker instructions are untested, and it says so.

If deferring again, **begin V0.4 — Planner / Executor**:

1. `git switch -c feat/v0.4-planner`
2. Add the `tasks` table per `docs/05-data-model.md` (it is specified and not yet created) —
   `task_type`, `parameters`, `depends_on UUID[]`, `state`, `attempt_count`, `max_attempts`.
3. Build in order: `Plan` schema → planner call returning a validated plan → **acyclicity check
   before persisting** → task rows → executor walking ready tasks → explicit state machine →
   bounded retry with exponential backoff and jitter → `SKIPPED` propagation.
4. The state machine is `docs/04-domain-model.md`. Transitions are enforced in code and raise on
   anything illegal — **the LLM never moves a task between states**.
5. Tests: exhaustive transition table including every illegal transition; cyclic plan rejected;
   retry then permanent failure; dependents skipped; `PARTIALLY_COMPLETED` produced.
6. Write `docs/11-orchestration.md` and `docs/17-failure-recovery.md` (stubs whose milestone
   arrives).
7. Watch the quota: a multi-task plan costs several calls per goal. Develop against
   `FakeProvider` and spend live calls only on the demo.

Full V0.4 spec and Definition of Done: `docs/19-roadmap.md`.
