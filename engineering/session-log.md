# Session Log

Appended at the end of every session. Newest last.

---

# Session 1

**Date:** 2026-09-03
**Module:** Phase 0 — Architecture & Master Roadmap
**Objective:** Analyse AMOS, design the architecture, produce foundational documentation and an
incremental roadmap. No application code.

## What We Changed
Created the repository from empty: working agreement, 10 written documents, 14 stubs, six
engineering logs, git history and a private GitHub remote.

## Files Changed
All files — the repository did not exist at the start of the session.
`CLAUDE.md`, `README.md`, `.gitignore`, `.env.example`, `docs/00`–`docs/23`,
`engineering/*.md`.

## Architecture Decisions
Eight ADRs (`docs/03-architecture-decisions.md`). The three that changed the project's
direction away from the original brief:

- **ADR-001 — pgvector instead of Qdrant.** The brief named Qdrant. Qdrant's advantages appear
  at a scale AMOS will not reach, while its cost — Postgres/Qdrant dual-write inconsistency —
  appears immediately.
- **ADR-002 — persistence before the planner.** The brief sequenced reliability last. A
  planner's output *is* state; producing it before it can be persisted or inspected makes it
  undebuggable, and retrofitting durability across three milestones is the expensive order.
- **ADR-007 — 10 documents written, 14 stubbed.** The brief asked for 24 documents up front and
  separately forbade placeholder documentation. Two thirds of them would have been guesses
  presented as decisions.

## Problems Encountered
1. **Disk at 96%** — 9.4 GB free on a partition shared with `/`. Anshul cleared ~110 GB during
   the session.
2. **Recalled technology facts were wrong.** Three, each of which would have caused a real
   failure. See below.
3. **The brief contradicted itself** on documentation (§20 asks for 24 docs; §20 also forbids
   placeholders).

## How We Solved Them
1. Anshul freed the space. Noted honestly in ADR-001 that this removed one of the three original
   arguments for pgvector, and that the decision stood on the remaining one.
2. Verified everything against primary sources rather than recall. Corrections recorded in
   `docs/21-technology-baseline.md`.
3. Resolved by ADR-007 in favour of the anti-placeholder rule, since a stub can still convey
   architectural intent while an invented decision cannot be un-cited.

## Tests Performed
No code, so no tests. Verification performed instead:
- All 20 documentation URLs checked live — every one returned HTTP 200.
- Library versions, Python support and model IDs confirmed against PyPI and official docs.
- pgvector's index dimension limit confirmed against the project README.

## Current System State
Documentation only. Nothing executable. `main` has no build to break.

## Things I Learned
Concepts logged in `engineering/learning-log.md`. The session-level lesson: **verify, do not
recall.** Three of three assumed facts about a fast-moving ecosystem were wrong, and one of them
(pgvector's 2000-dimension index limit) would only have surfaced at V0.5, after an entire corpus
had been embedded at the wrong dimension.

## Things I Should Investigate
- Does `gemini-3.5-flash` structured output reliably return valid JSON, or is the repair loop
  load-bearing in practice? Measure the repair rate at V0.1 rather than assuming.
- Confirm free-tier rate limits from the account dashboard — published tables are no longer
  authoritative per model.

## References
- <https://ai.google.dev/gemini-api/docs/models>
- <https://github.com/pgvector/pgvector>
- <https://googleapis.github.io/python-genai/>

## Next Exact Step
Begin V0.1 per `engineering/current-state.md` → *Exact Next Step*. First action outside the
repository: obtain a Gemini API key.

## Recommended Commit
Already committed across seven commits on `main` and pushed. No outstanding changes.

---

# Session 2

**Date:** 2026-09-03
**Module:** V0.1 — Grounded Agent API
**Objective:** Ship the first runnable milestone: typed API, provider abstraction, structured
output with bounded repair, tests that never touch the network.

## What We Changed
Built V0.1 end to end. 15 source files, 5 test files, 41 tests. Wrote `docs/06-api-specification.md`
and `docs/interview/foundation.md` (both stubs whose milestone arrived). Updated README with real
run instructions and verified output.

## Files Changed
`pyproject.toml`, `src/amos/**` (config, errors, observability, llm/, agents/, api/, `__main__`),
`tests/**` (conftest, unit×3, integration×1, live×1), `README.md`,
`docs/06-api-specification.md`, `docs/interview/foundation.md`, `docs/22-resume-evidence.md`,
`engineering/{current-state,session-log,bugs-log,experiments-log}.md`.

## Architecture Decisions
No new ADRs — V0.1 implements decisions already made. Two implementation choices worth recording:

- **`Protocol` over ABC for `LLMProvider`.** Structural typing means the fake substitutes
  without inheritance. Justified by V0.1's own testing need, not by future providers.
- **`502` for `OutputValidationError`.** The request was valid and AMOS worked correctly; an
  upstream dependency failed. `500` would send someone debugging the wrong system.

## Problems Encountered
1. `TypeError: log_event() got multiple values for argument 'message'` — every error path broken.
2. `python -m amos` → `ModuleNotFoundError` despite pip reporting the package installed.
3. Two `mypy --strict` errors, one of which was a genuine logic defect:
   `isinstance(response.parsed, object)` — always true, so it narrowed nothing.

## How We Solved Them
1. Renamed the structured field to `error_message`. Caught by the error-path integration tests,
   which the happy path would never have exercised.
2. Added `[tool.hatch.build.targets.editable] dev-mode-dirs = ["src"]`. Caught by *running the
   app* — the test suite could not have caught it, since pytest's own `pythonpath` masked the
   broken install.
3. Narrowed properly to `isinstance(response.parsed, BaseModel)`, and typed `app.state.agent`.

## Tests Performed
- 41 tests pass, 1 skipped (live, correctly opt-in). No network access.
- `mypy --strict` clean across 15 files; `ruff check` and `ruff format` clean.
- Live smoke test against real Gemini: passed, `repair_count=0`, 183 tokens.
- Full manual demo: health, a real goal, a 422 validation error, structured logs inspected.

## Current System State
V0.1 shipped and tagged. `main` runs and its tests pass.

## Things I Learned
- **A green test suite does not prove the app starts.** Problem 2 is the clearest possible
  demonstration: 41 passing tests alongside a package that could not be imported. The Definition
  of Done requires running the demo for exactly this reason.
- **Error paths need testing as deliberately as happy paths.** Problem 1 lived entirely in code
  a manual demo never reaches.
- `isinstance(x, object)` is always true. A type checker caught a "check" that checked nothing.

## Things I Should Investigate
- ~16s latency on a single call — model, network, or this connection? Matters at V0.4, where a
  plan means several sequential calls.
- Does the repair loop ever fire against `gemini-3.5-flash`? 0/3 so far; n=3 proves nothing.

## References
- <https://googleapis.github.io/python-genai/>
- <https://ai.google.dev/gemini-api/docs/structured-output>
- <https://docs.python.org/3/library/typing.html#typing.Protocol>

## Next Exact Step
**The advance gate is unmet** — the V0.1 pre-read has not been done, so `docs/interview/foundation.md`
cannot yet be answered unaided. Per `CLAUDE.md`, V0.2 waits on that. If deliberately deferred,
V0.2 (Tool Registry) is specified in `engineering/current-state.md`.

## Recommended Commit
Committed and pushed across three commits; merged to `main` and tagged `v0.1`.

---

# Session 3

**Date:** 2026-09-03
**Module:** V0.2 — Tool Registry
**Objective:** Give the agent tools it can select and execute autonomously, with the system
enforcing what it may touch.

## What We Changed
Built the tool system: `Tool` ABC, registry, bounded agent loop, three safe tools. Extended the
provider seam for function calling without touching V0.1's agent. Wrote
`docs/08-tool-specification.md`, `docs/13-security.md`, `docs/15-testing.md` and
`docs/interview/agents.md`. 117 tests.

## Files Changed
`src/amos/tools/**` (base, registry, builtin×3), `src/amos/agents/tool_agent.py`,
`src/amos/llm/{base,gemini,fake}.py`, `src/amos/{errors,config}.py`, `src/amos/api/{app,dependencies}.py`,
`tests/unit/tools/**`, `tests/unit/test_tool_agent.py`, `tests/integration/*`, `tests/live/*`,
`docs/{08,13,15,21,22}`, `docs/interview/agents.md`, `engineering/*`.

## Architecture Decisions
- **`Tool` is an ABC, not a Protocol.** Providers share a shape; tools share *behaviour*
  (validate + timeout). Putting that in a concrete `execute()` means a tool cannot opt out of it.
- **Tool declarations generated from the Pydantic input schema.** One source of truth, so what
  the model is told and what the code validates cannot drift.
- **Failures are data, not exceptions.** Every outcome is fed back so the model can correct
  itself; the iteration cap still guarantees termination.
- **`WRITE`/`DESTRUCTIVE` refused by the registry**, not merely undocumented.
- **Default model → `gemini-3.5-flash-lite`**, because free-tier quota is per model and daily.

## Problems Encountered
1. `400 INVALID_ARGUMENT: Function call is missing a thought_signature` on the second round trip.
2. `429` after ~20 live calls — far sooner than "15 requests/minute" predicted.
3. A docstring claiming, as *verified*, that tools and `response_schema` could not be combined.
4. Integration tests broke when the API's agent type changed.

## How We Solved Them
1. `Turn.provider_state` — an opaque field carrying the vendor's original content, replayed
   verbatim. Only the producing provider reads it. ADR-005 predicted needing this hatch.
2. Read the full quota violation rather than the status code: **20 requests/day per model**, not
   15/minute. Switched the default model, fixed the wrong error message, and removed a wasted
   call per goal.
3. Tested it. The combination is allowed. Removed the third API call: 3 → 2 calls per goal,
   8.4s → 2.8s.
4. Rewrote them for `ToolUsingAgent` and gave V0.1's agent its own file rather than deleting its
   coverage. Gave both agents a `tool_names` property so the API layer need not special-case.

## Tests Performed
- 117 pass, 2 skipped (live, opt-in). No network.
- `mypy --strict` clean across 23 files; `ruff` clean.
- Live: calculator tool loop end to end.
- Demo: arithmetic via tool; AMOS reading its own ADRs and explaining the pgvector decision;
  `../../../../etc/passwd` traversal refused with the model reporting the refusal honestly.

## Current System State
V0.2 shipped and tagged. `main` runs and its tests pass.

## Things I Learned
- **Documentation is not behaviour.** Three separate facts were wrong in published docs or
  untested: the free-tier limit, `gemini-2.5-flash`'s availability, and the tools+schema
  combination. Only the live API settled them.
- **Never write "verified" for something assumed.** A confident annotation stopped me
  re-examining the claim; the phrasing did more damage than the wrong belief.
- **Fakes cannot test what they do not model.** `FakeProvider` has no thought signatures to
  lose, so every scripted test passed while the real API rejected the request. That is the
  argument for keeping an opt-in live test.
- Extending the provider seam without touching `GroundedAgent` or its 11 tests was the first
  real evidence the V0.1 design held.

## Things I Should Investigate
- Does `_finalise` ever fire now? If not by V0.4, delete it rather than carry untested code.
- What is `gemini-3.5-flash-lite`'s actual daily quota? Only `flash`'s 20/day was measured.
- Quality difference between lite and flash on tool selection — matters before V0.7 routing.

## References
- <https://ai.google.dev/gemini-api/docs/function-calling>
- <https://ai.google.dev/gemini-api/docs/thinking#signatures>
- <https://ai.google.dev/gemini-api/docs/rate-limits>

## Next Exact Step
V0.3 — Persistence and Trace. **Docker must be installed first.** Full sequence in
`engineering/current-state.md`. Advance gate still unmet: `docs/interview/{foundation,agents}.md`.

## Recommended Commit
Committed and pushed; merged to `main` and tagged `v0.2`.
