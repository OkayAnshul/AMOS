# Current State

> **Read this first when resuming.** It is the recovery mechanism — it must be sufficient to
> restart cold after months away, without conversation history.

**Last updated:** 2026-09-03 (Session 1)

## Current Version
**Phase 0** — architecture and roadmap complete. Pre-V0.1.

## Current Module
None. No application code exists yet, by design.

## Completed Modules
- **Phase 0** — architecture, ADRs, domain model, data model, roadmap, engineering logs.

## What Works
Documentation only. There is nothing executable in this repository.

## What Does Not Work
Everything — nothing is built. Specifically absent: API, agent, tools, database, tests, and
`pyproject.toml`. This is the expected state after Phase 0.

## Current Architecture
Planned, not built:
```
V0.1:  Client → FastAPI → Agent → LLMProvider → Gemini      (no database, ADR-006)
```
Target architecture: `docs/02-system-architecture.md`.

## Current Branch
`main`

## Last Known Good Commit
The most recent commit on `main`. All Phase 0 commits are documentation; none can break a build,
because there is no build.

## Known Bugs
None. There is no code to have bugs.

## Technical Debt
None yet. One thing to watch: the `AgentResult` envelope built in V0.1 must carry the fields
that become the `steps` row at V0.3 (`docs/02-system-architecture.md`, seams table). Getting
that wrong in V0.1 creates a migration at V0.3.

## Environment
| Thing | State |
|---|---|
| Python | 3.14.4 (only version installed) |
| Docker | **not installed** — needed at V0.3, not before |
| PostgreSQL | not installed — V0.3 |
| Virtualenv | not created yet — V0.1 |
| `GEMINI_API_KEY` | **not yet obtained** — needed for V0.1 |
| Disk | ~119 GB free |
| GitHub | `OkayAnshul/AMOS`, **private** — goes public at v0.1 |
| Git remote | SSH over **port 443** (`ssh://git@ssh.github.com:443/...`) — port 22 is blocked on this network, see `bugs-log.md` |

## How To Run
Nothing to run yet. V0.1 will add instructions here and in the README.

## How To Test
No tests yet. V0.1 will add `pytest`.

## Exact Next Step

**Begin V0.1 — Grounded Agent API.** In order:

1. Get a Gemini API key from <https://aistudio.google.com/apikey>, put it in `.env`
   (copy from `.env.example`). `.env` is gitignored.
2. Create the branch: `git switch -c feat/v0.1-grounded-agent`
3. Create `pyproject.toml`, the virtualenv, and pin: FastAPI ≥0.119.1, Pydantic ≥2.12,
   pydantic-settings, `google-genai` ≥2.21.0, pytest, ruff, mypy.
4. Build in this order: settings → `LLMProvider` protocol + `FakeProvider` → `GeminiProvider`
   → `AgentResponse` schema → validate-and-repair loop → FastAPI endpoint → structured logging.
   **Tests are written alongside each piece, not at the end.**
5. Before writing code, read the V0.1 pre-read links in `engineering/learning-log.md`.

Full V0.1 specification, including its Definition of Done: `docs/19-roadmap.md`.

**Advance gate:** V0.2 does not begin until the V0.1 questions in `engineering/learning-log.md`
can be answered unaided.
