# Current State

> **Read this first when resuming.** It is the recovery mechanism — it must be sufficient to
> restart cold after months away, without conversation history.

**Last updated:** 2026-09-03 (Session 2)

## Current Version
**V0.1 — Grounded Agent API.** Shipped, tagged `v0.1`.

## Current Module
None in progress. Next milestone is V0.2 (Tool Registry), **blocked on the advance gate**.

## Completed Modules
- **Phase 0** — architecture, ADRs, domain model, data model, roadmap.
- **V0.1** — FastAPI service, `LLMProvider` protocol, Gemini provider, structured output with
  bounded repair, typed errors, structured logging with request ids. 41 tests.

## What Works
- `POST /v1/goals` → validated `AgentResponse` (answer, reasoning, assumptions, confidence, caveats)
- `GET /health`, `GET /docs`, `GET /openapi.json`
- Provider swapping via the `LLMProvider` protocol (`GeminiProvider`, `FakeProvider`)
- Repair loop: malformed output re-prompted with the error, bounded, then `OutputValidationError`
- Typed errors mapped to status codes in one table (`api/app.py::_STATUS_MAP`)
- Structured JSON logs with a request id, echoed as `x-request-id`
- 41 tests passing, none touching the network; 1 live smoke test opt-in
- `mypy --strict` and `ruff` clean
- Verified live against real Gemini: 362 tokens, ~16s, `repair_count=0`

## What Does Not Work
Nothing is broken. Not yet built: tools (V0.2), persistence (V0.3), planner (V0.4), RAG (V0.5),
memory (V0.6), multi-agent (V0.7), async (V0.8), tracing (V0.9), evaluation (V1.0).

Known limitation: **nothing survives a restart.** Deliberate — ADR-006.

## Current Architecture
```
Client → FastAPI → GroundedAgent → LLMProvider → Gemini
                        ↓
                  repair loop (bounded)
```
No database. Target architecture: `docs/02-system-architecture.md`.

## Current Branch
`main` (V0.1 merged from `feat/v0.1-grounded-agent`)

## Last Known Good Commit
Tag `v0.1` — tests pass, app runs, demo verified end to end.

## Known Bugs
None open. Two fixed during V0.1, both in `engineering/bugs-log.md`.

## Technical Debt
- `GroundedAgent._validate` handles three shapes of provider output (typed, dict-ish, raw text).
  Correct but slightly baroque; revisit if a second provider makes the branching worse.
- Live latency is ~16s for one call. Unmeasured whether that is the model, the network, or
  `gemini-3.5-flash` specifically. Worth checking before V0.4, where a plan means several
  sequential calls.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4 |
| Virtualenv | `.venv/` — `.venv/bin/pip install -e ".[dev]"` |
| Installed | fastapi 0.141.1, pydantic 2.13.5, google-genai 2.22.0 |
| `.env` | **exists with a working key** (gitignored) |
| Docker / PostgreSQL | not installed — needed at V0.3 |
| Git remote | SSH over **port 443** — port 22 is blocked on this network, see `bugs-log.md` |
| GitHub | `OkayAnshul/AMOS` |

## How To Run
```bash
.venv/bin/python -m amos          # http://127.0.0.1:8000
curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Explain idempotency in one paragraph."}' | jq
```

## How To Test
```bash
.venv/bin/python -m pytest                                   # 41 tests, no network
.venv/bin/mypy src && .venv/bin/ruff check src tests
AMOS_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live  # opt-in, real API
```

## Exact Next Step

**The advance gate is open and unmet.** Per `CLAUDE.md`, V0.2 does not start until the questions
in `docs/interview/foundation.md` can be answered unaided. Anshul has not yet done the V0.1
pre-read (`engineering/learning-log.md`).

If the gate is being deliberately deferred, **begin V0.2 — Tool Registry**:

1. `git switch -c feat/v0.2-tool-registry`
2. Build in order: `Tool` base (name, description, input/output schema, timeout, permissions) →
   registry with decorator registration → schema export to Gemini function calling → bounded
   agent loop → the three tools.
3. Tools: `calculator` (pure), `http_get` (**domain allowlist** — N-11), `read_file` (sandboxed
   path). All deterministic and reversible; no payments, no deletion, no sending.
4. Security tests are not optional here: path traversal on `read_file`, off-allowlist URL on
   `http_get`, hallucinated tool name, invalid arguments, loop cap.
5. Write `docs/08-tool-specification.md` and `docs/13-security.md` — both are stubs whose
   milestone has now arrived.

Full V0.2 specification and Definition of Done: `docs/19-roadmap.md`.
