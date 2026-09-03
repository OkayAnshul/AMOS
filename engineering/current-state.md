# Current State

> **Read this first when resuming.** It is the recovery mechanism — it must be sufficient to
> restart cold after months away, without conversation history.

**Last updated:** 2026-09-03 (Session 3)

## Current Version
**V0.2 — Tool Registry.** Shipped, tagged `v0.2`.

## Current Module
None in progress. Next is V0.3 (Persistence + Trace).

## Completed Modules
- **Phase 0** — architecture, ADRs, domain model, data model, roadmap.
- **V0.1** — FastAPI service, `LLMProvider` protocol, structured output with bounded repair.
- **V0.2** — `Tool` ABC, registry, bounded tool loop, three safe tools, security tests.

## What Works
- `POST /v1/goals` → the agent autonomously selects and executes tools, then answers
- Tools: `calculator` (AST-walked, no `eval`), `read_file` (sandboxed), `http_get` (allowlisted)
- Arguments validated against the Pydantic schema **before** execution; declarations generated
  from that same schema
- Every failure fed back to the model as data: hallucinated tool, invalid args, timeout, error
- Hard iteration cap; `ToolLoopExhaustedError` → 502
- `WRITE`/`DESTRUCTIVE` permissions refused by the registry itself
- V0.1's `GroundedAgent` still works and is still tested (`tests/integration/test_grounded_api.py`)
- 117 tests, none touching the network; 2 live tests opt-in
- `mypy --strict` and `ruff` clean

**Verified live:** arithmetic via calculator (2 calls, ~2.1s); AMOS reading its own
`docs/03-architecture-decisions.md` and explaining the pgvector decision; a `../../../../etc/passwd`
traversal attempt correctly refused.

## What Does Not Work
Nothing is broken. Not built: persistence (V0.3), planner (V0.4), RAG (V0.5), memory (V0.6),
multi-agent (V0.7), async (V0.8), tracing (V0.9), evaluation (V1.0).

**Nothing survives a restart** — deliberate, ADR-006. V0.3 changes this.

## ⚠️ Operating constraint: 20 requests/day
The Gemini free tier is a **daily per-model quota**, not a rate limit:
`GenerateRequestsPerDayPerProjectPerModel-FreeTier, limit: 20`. A tool-using goal costs 2 calls,
so ≈10 goals/day/model.

- `AMOS_LLM_MODEL` defaults to **`gemini-3.5-flash-lite`** — quota is per model, so development
  does not consume the allowance kept for demos with `gemini-3.5-flash`.
- `gemini-2.5-flash` is **404 / no longer served**.
- Tests must never call the network (N-14) — one run could exhaust a day.

## Current Architecture
```
Client → FastAPI → ToolUsingAgent ⇄ ToolRegistry → {calculator, read_file, http_get}
                        ↓
                   LLMProvider → Gemini
```
No database. Target: `docs/02-system-architecture.md`.

## Current Branch
`main` (V0.2 merged from `feat/v0.2-tool-registry`)

## Last Known Good Commit
Tag `v0.2` — tests pass, app runs, all three demos verified.

## Known Bugs
None open. Four fixed across V0.1–V0.2, all in `engineering/bugs-log.md`.

## Technical Debt
- `ToolUsingAgent._finalise` is now a rarely-taken fallback. If it never fires by V0.4, delete it
  rather than carrying untested code.
- `GroundedAgent._validate` handles three output shapes; revisit if a second provider worsens it.
- No CI. Tests only run when someone runs them.
- Tool outcomes are returned to the caller and logged, but not persisted — V0.3.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4 |
| Virtualenv | `.venv/` — `.venv/bin/pip install -e ".[dev]"` |
| Installed | fastapi 0.141.1, pydantic 2.13.5, google-genai 2.22.0, httpx |
| `.env` | exists with a working key (gitignored) |
| Docker / PostgreSQL | **not installed — needed for V0.3** |
| Git remote | SSH over **port 443** (port 22 blocked on this network) |
| GitHub | `OkayAnshul/AMOS`, public |

## How To Run
```bash
.venv/bin/python -m amos          # http://127.0.0.1:8000
curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"What is 17% of 2340 plus 88?"}' | jq
```

## How To Test
```bash
.venv/bin/python -m pytest                                   # 117 tests, no network
.venv/bin/mypy src && .venv/bin/ruff check src tests
AMOS_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live  # opt-in, uses daily quota
```

## Exact Next Step

**Advance gate: unmet.** `docs/interview/foundation.md` (V0.1) and `docs/interview/agents.md`
(V0.2) have not been worked through. Per `CLAUDE.md`, V0.3 waits on that.

If deferring again, **begin V0.3 — Persistence and Trace**:

1. `git switch -c feat/v0.3-persistence`
2. **Install Docker first** — it is not on this machine. Then `docker compose up` with one
   service: PostgreSQL 18 + pgvector (`pgvector/pgvector:pg18` or equivalent).
3. Build in order: SQLAlchemy 2.0 async engine and session → Alembic baseline migration →
   schema from `docs/05-data-model.md` (`runs`, `tasks`, `steps`, `llm_calls`, `tool_calls`) →
   repository layer → persist what `AgentResult` already carries → `GET /v1/runs/{id}` →
   idempotency key on submit.
4. `AgentResult.llm_calls` and `.tool_outcomes` are **already shaped like the rows** — this
   should be a serialisation change, not a redesign. If it turns into a redesign, the V0.1/V0.2
   seams were wrong and that is worth writing down.
5. Tests: transactional rollback fixtures, trace completeness, idempotent resubmit, migration
   up/down.
6. Write `docs/18-deployment.md` (stub whose milestone arrives).

Full V0.3 spec and Definition of Done: `docs/19-roadmap.md`.
