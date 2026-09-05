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

---

# Session 4

**Date:** 2026-09-03
**Module:** V0.3 — Persistence and Trace
**Objective:** Make every run durable and inspectable. The hinge milestone: persistence
independently unblocks the planner, RAG and async execution.

## What We Changed
PostgreSQL 18 + pgvector via one podman service. SQLAlchemy 2.0 async models, Alembic baseline
migration, repository layer, `RunService`, `GET /v1/runs/{id}`, idempotency keys. Wrote
`docs/18-deployment.md` and `docs/interview/persistence.md`. 136 tests.

## Files Changed
`compose.yaml`, `alembic.ini`, `migrations/**`, `src/amos/database/**`,
`src/amos/api/{app,persistence}.py`, `src/amos/{config,agents/schemas}.py`,
`src/amos/tools/base.py`, `tests/integration/{conftest,test_persistence,test_trace_api}.py`,
`tests/conftest.py`, `docs/{18,22}`, `docs/interview/persistence.md`, `engineering/*`.

## Architecture Decisions
No new ADRs. Implementation decisions worth recording:
- **Run row written before execution.** A crash then still leaves evidence it was attempted.
- **Execution outside any transaction.** An LLM call takes seconds; holding a pooled connection
  across it would deadlock the pool at six concurrent goals.
- **`run_id` denormalised onto `llm_calls`/`tool_calls`.** Trace assembly is the hottest read;
  this makes it one indexed filter per table.
- **Persistence is optional.** Without `AMOS_DATABASE_URL` the app still runs and 118 of 136
  tests pass. Mandatory infrastructure would break "every milestone is runnable".

## Problems Encountered
1. `postgres:18` container exited(1) immediately after `compose up` reported success.
2. 10 of 11 database tests failed with "attached to a different loop".
3. Adding `AMOS_DATABASE_URL` to `.env` broke 12 previously-passing integration tests.
4. Podman/Docker needed installing — neither was present, and pgvector is not in Arch's repos.

## How We Solved Them
1. Read the container logs. PostgreSQL 18 changed the volume convention to
   `/var/lib/postgresql` (not `/data`). Added a healthcheck so a non-starting container is
   visible rather than merely absent.
2. Made the engine fixture function-scoped with `NullPool`. asyncpg connections belong to the
   event loop that created them, and pytest-asyncio gives each test its own.
3. `isolated_settings()` building `Settings(_env_file=None, …)`. The bug had been latent since
   V0.1 and only surfaced when `.env` gained a setting that changed behaviour.
4. Chose podman — rootless, so no docker group and no logout/login. `compose.yaml` is written
   for both; **only the podman path is verified**, and the deployment doc says so.

## Tests Performed
- 136 pass with the database, 118 pass + 18 skip without it — verified by stopping the container
  and re-running.
- `alembic upgrade head` → `downgrade base` → `upgrade head`, confirming table counts each way.
- `mypy --strict` clean across 27 files; `ruff` clean.
- Demo: goal → `run_id` → full trace from stored rows; idempotent resubmit returned the same run.

## Current System State
V0.3 shipped and tagged. `main` runs and its tests pass.

## Things I Learned
- **The seams held, and I can now say that with evidence rather than hope.** `_add_trace_rows`
  is a mechanical field copy — no field had to be derived or restructured. That was V0.1/V0.2's
  central bet and it paid off.
- **One gap, worth stating plainly:** `ToolOutcome` recorded a tool's result but not its
  arguments, so the first working trace showed what came back without what was asked. The design
  was right in shape and ~95% right in content. Claiming it worked perfectly would have been the
  less useful record.
- **"The container started" is not "the service is running."** `compose up` exited 0 while
  Postgres was dead. Healthchecks belong in the first version of a compose file.
- **A test that reads `.env` depends on the machine it runs on.** Latent for two milestones.
- **Async fixtures must not outlive their event loop.** When the first test passes and the rest
  fail identically, suspect the fixtures.

## Things I Should Investigate
- Verify `compose.yaml` on Docker; the deployment doc currently documents an unverified path.
- `_finalise` has still never fired. Delete it at V0.4 if it stays dead.
- Latency was 12s for a 2-call goal here vs 2.1s at V0.2 — network variance again, but worth
  watching before V0.4 makes calls sequential.

## References
- <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html>
- <https://alembic.sqlalchemy.org/en/latest/tutorial.html>
- <https://github.com/docker-library/postgres/issues/37> (the PG18 volume path change)

## Next Exact Step
V0.4 — Planner / Executor. Full sequence in `engineering/current-state.md`. Advance gate now has
three unread interview docs.

## Recommended Commit
Committed and pushed; merged to `main` and tagged `v0.3`.

---

# Session 5

**Date:** 2026-09-05
**Module:** V0.4 — Planner / Executor
**Objective:** Decompose goals into a durable task DAG with deterministic state management,
bounded retries and contained failure.

## What We Changed
`src/amos/orchestration/` — state machine, plan validation, planner, executor, retry policy,
orchestrator. `tasks` table plus `steps.task_id`. Alembic metadata naming convention. Trace now
carries the task DAG. Wrote `docs/11-orchestration.md`, `docs/17-failure-recovery.md`,
`docs/interview/orchestration.md`. 266 tests (up from 136).

## Files Changed
`src/amos/orchestration/**` (state, plan, planner, executor, retry, orchestrator),
`src/amos/database/{models,repository}.py`, `src/amos/api/{app,dependencies,persistence}.py`,
`src/amos/{config,agents/schemas}.py`, `migrations/versions/*`,
`tests/unit/orchestration/**`, `tests/integration/test_persistence.py`,
`docs/{11,17,22}`, `docs/interview/orchestration.md`, `engineering/*`.

## Architecture Decisions
- **Only `orchestration/state.py` changes a task's state**, via `assert_transition`, which raises
  on anything the table forbids. This is the invariant "LLMs handle uncertainty, software handles
  guarantees" made executable rather than aspirational.
- **A retry returns the task to `READY`**, not a retry-specific state — one code path for "about
  to run", so a retried attempt cannot diverge from a first one.
- **`depends_on` as a Postgres `UUID[]`**, not a join table; every read loads the whole graph
  anyway. Stored as row UUIDs, so the graph survives without the plan text.
- **Synthesis skipped for single-task plans and total failures** — a measurable fraction of a
  20-call daily budget, not a micro-optimisation.

## Problems Encountered
1. `alembic downgrade` failed: `Can't emit DROP CONSTRAINT ... it has no name`.
2. mypy flagged `2**attempt` widening to `Any`, silently making a return type unchecked.
3. A `match` statement reusing the name `func` across arms unified two incompatible signatures.

## How We Solved Them
1. Added a `naming_convention` to `Base.metadata` so every constraint has a deterministic,
   droppable name. This required a fresh baseline, which **squashed V0.3's migration** — a
   history rewrite of a shipped artifact, safe only because it had run on exactly one machine.
   Recorded as such rather than glossed.
2. Annotated the intermediate explicitly. A widened `Any` return type is the kind of thing strict
   typing exists to catch and comments do not.
3. Renamed the unary branch's binding. Reusing a name across `match` arms is legal and makes the
   checker unify types that have nothing to do with each other.

## Tests Performed
- 266 pass, 2 skipped (live). Includes **all 53 illegal state transitions** asserted to raise,
  plus a test asserting the module's transition table and the test's expectations agree — so they
  cannot drift.
- `alembic upgrade → downgrade base → upgrade`, verified by table count each way.
- `mypy --strict` clean across 34 files; `ruff` clean.
- Live demo: one goal → 3-task diamond DAG, two branches concurrent, correct answer, trace
  persisted with dependencies resolved to row UUIDs.

## Current System State
V0.4 shipped and tagged. `main` runs and its tests pass.

## Things I Learned
- **An irreversible migration is a one-way door you find at the worst moment.** The upgrade was
  perfect; the defect only existed in the reverse direction, and only appeared because reversing
  it was part of the Definition of Done.
- **Testing only legal transitions tests almost nothing.** The value is in the 53 illegal ones —
  and in the test that keeps the table and its expectations in sync, since otherwise adding a
  transition silently shrinks the illegal set.
- **Jitter needs its own test.** `test_jitter_actually_varies` exists because a constant would
  pass every bounds check while doing nothing to decorrelate retries.
- Writing `docs/17-failure-recovery.md`'s "deliberately NOT handled" table was more useful than
  the handled one. Naming that tasks are not idempotent — safe today only because every tool is
  read-only — is the gap most likely to become a real bug.

## Things I Should Investigate
- `_finalise` has now survived two milestones without ever firing. Delete it at V0.5.
- Planner quality is n=1. Routing/planning accuracy only becomes measurable at V1.0.
- `steps` is still one row per run; the schema supports one per attempt and the repository does
  not use it. Worth closing before V0.8 makes attempts more interesting.

## References
- <https://alembic.sqlalchemy.org/en/latest/naming.html>
- <https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#sqlalchemy.dialects.postgresql.ARRAY>

## Next Exact Step
V0.5 — RAG. Full sequence in `engineering/current-state.md`. The embedding dimension decision
(1536, re-normalised) is already made in ADR-008 and is a silent-failure trap if skipped.

## Recommended Commit
Committed and pushed; merged to `main` and tagged `v0.4`.
