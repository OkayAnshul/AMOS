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

---

# Session 6

**Date:** 2026-09-05
**Module:** V0.5 — Retrieval (RAG)
**Objective:** A real retrieval pipeline with citations and a **measured** recall figure — not a
vector database with a claim attached.

## What We Changed
`src/amos/rag/` — embeddings with mandatory re-normalisation, heading-aware chunking, VectorStore
protocol with pgvector and in-memory implementations, ingestion with content hashing, retrieval as
a Tool, and an evaluation harness. pgvector migration. Ingest/evaluate CLI. Wrote
`docs/10-rag-architecture.md` with measured numbers and `docs/interview/rag.md`. 314 tests.

## Files Changed
`src/amos/rag/**`, `src/amos/database/models.py`, `src/amos/api/{app,dependencies}.py`,
`src/amos/agents/tool_agent.py`, `src/amos/config.py`, `migrations/versions/*_v0_5_*`,
`tests/unit/rag/**`, `tests/integration/test_persistence.py`, `docs/{10,22}`,
`docs/interview/rag.md`, `engineering/*`.

## Architecture Decisions
- **Re-normalise every truncated embedding at the provider boundary**, with a test — not a
  documented intention.
- **Heading-aware chunking**, heading prepended to each of its chunks.
- **Retrieval as a Tool**, so the agent decides; inherits validation, timeouts and trace visibility.
- **Empty retrieval returns a refusal instruction**, never an empty list.
- **Ingestion commits per document** — see the bug below.
- **Ground truth is multi-source**, with strict and lenient recall both reported permanently.

## Problems Encountered
1. First ingest hit a 429 and **rolled back all 300 chunks** — every embedded chunk lost.
2. The embedding quota behaved nothing like the chat quota.
3. `recall@1 = 50%` on the first golden set, which looked like bad retrieval.

## How We Solved Them
1. One transaction **per document** rather than one for the corpus, plus paced batches and retry
   honouring the provider's own `retryDelay`.
2. Read the 429 body: **100 per minute**, counting *contents* not requests — so batching reduces
   round trips but not quota, and unlike the daily chat quota, waiting actually works.
3. Looked at *what* was retrieved instead of just the score. Every miss had returned an
   `interview/*.md` doc — Q&A-formatted, and a genuinely correct answer. The labels were wrong,
   not the retrieval. Widened ground truth, and report both figures permanently.

## Tests Performed
- 314 pass, 2 skipped. Includes a test asserting truncated vectors are not unit length without
  re-normalisation, and one asserting the fake embedder makes similar texts similar (a random fake
  would render every retrieval assertion meaningless).
- pgvector integration tests at the real 1536 dimensionality.
- Migration reverses (`upgrade → downgrade → upgrade`, table counts checked).
- Live: ingested 28 docs / 300 chunks; re-ingest reported `0 documents (28 unchanged)`; measured
  recall at k=1/3/5; two grounded demos including the refusal path.

## Current System State
V0.5 shipped and tagged. `main` runs and its tests pass.

## Things I Learned
- **A metric that disagrees with another metric is telling you something.** MRR was 0.917 at k=1
  while single-label recall said 50%. The right document was almost always rank 1 the whole time.
  When two measures disagree sharply, suspect the measurement before the system.
- **Widening ground truth after seeing results is exactly how metrics get massaged.** Doing it was
  correct here — the retrieved docs genuinely answer the questions — but it only stays honest
  because both numbers are reported and the rule is written into the code the next person edits.
- **Transaction boundaries should follow units of useful work, not units of code.** 300 chunks in
  one transaction reads as "atomic" and means "lose everything on any failure".
- **Fakes cannot rate-limit.** No unit test would have found the ingest rollback; only a real
  corpus against a real quota did. Same lesson as V0.2's thought_signature, in a new costume.
- The normalisation trap was real and large: 31% off unit length, accepted silently by pgvector.

## Things I Should Investigate
- Chunk-size sweep — 1000/150 was reasoned, never compared. The harness now exists.
- Hybrid search: an exact identifier like `SKIP LOCKED` is probably better found by keyword.
- Whether the interview docs dominating question-shaped queries is a corpus property worth
  exploiting (index them separately?) or a distortion to control for.
- `_finalise` has now been dead for three milestones. Delete it.

## References
- <https://ai.google.dev/gemini-api/docs/embeddings>
- <https://github.com/pgvector/pgvector>

## Next Exact Step
V0.6 — Memory. The real deliverable is `docs/09-memory-architecture.md`: deciding which store
each memory kind belongs in. **Not everything belongs in vectors** — exact recall of a stated fact
is a relational lookup, and similarity search will occasionally return the wrong person's fact.

## Recommended Commit
Committed and pushed; merged to `main` and tagged `v0.5`.

---

# Session 7

**Date:** 2026-09-06
**Module:** Documentation only — no application code changed.
**Objective:** Produce the complete reading list: prerequisites from zero knowledge, architecture
and design, every milestone built and unbuilt, and every algorithm mapped to the file that
implements it.

## What We Changed
Added `docs/24-study-plan.md`. Cross-linked it from `docs/20-learning-roadmap.md` and
`engineering/learning-log.md`, which previously had pre-read links for **V0.1 only** — V0.2–V0.5
were built with their concepts named in a table but never given reading.

## Files Changed
`docs/24-study-plan.md` (new), `docs/20-learning-roadmap.md`, `engineering/learning-log.md`,
`engineering/current-state.md`, `engineering/session-log.md`.

## What The Document Contains
- Tier 0 prerequisites — Python, async, HTTP, JSON Schema, SQL, containers, testing, tooling.
- Tier A — architecture and design: ports and adapters, repository/service/unit of work,
  monolith vs microservices, ADR practice, C4, designing for failure, agent architecture.
- Tiers 1–5 — one per shipped milestone, each naming its algorithms with `file:line`.
- Tiers 6–10 — pre-read for V0.6–V1.0, to be read *before* each milestone starts.
- A single table of all 24 algorithms currently in the repo, plus 6 not yet built.
- Four books, a suggested sequence, and an explicit **what to skip** list.

## Problems Encountered
Every URL was checked with `curl` before being written down (N: never fabricate a link).
Two failed and were replaced: the Grafana RED-method post (403 to a scripted request) was
dropped in favour of the SRE book's golden-signals chapter, and an EnterpriseDB `SKIP LOCKED`
post returned 404 — the original 2ndQuadrant URL resolves and was used instead.
`starlette.io` and `uvicorn.org` were unreachable from this network; the GitHub repositories
were used instead.

## Learning Notes
The reading list is long by design and is tagged **[core] / [deep] / [ref]** so it can be
truncated honestly. Roughly 70–90 hours of **[core]** covers Tier 0 through V0.5.

## Next Exact Step
Unchanged: **the advance gate is still unmet.** Five interview docs
(`docs/interview/{foundation,agents,persistence,orchestration,rag}.md`) remain unanswered.
The reading list now exists to close them. After that, V0.6 — Memory.

## Recommended Commit
`docs: add the complete study plan — prerequisites, architecture, algorithms`

---

# Session 8

**Date:** 2026-09-09
**Module:** V0.6 — Memory
**Objective:** Facts that survive a restart; past runs findable by similarity. Plus a build journal
covering the whole project from zero.

## What We Changed
`src/amos/memory/` — semantic store with normalised keys and a supersession chain, episodic recall
over `runs`, three memory tools. Migration for `memories` + episodic columns. Wrote
`docs/09-memory-architecture.md`, `docs/interview/memory.md`, and **`docs/25-build-journal.md`**
(the construction narrative Anshul asked for). Hardened the database readiness probe. 350 tests.

## Architecture Decisions
- **Facts are relational-first**, with vectors as a secondary index. Exact recall is a key lookup;
  contradictions need ordering; provenance is a join.
- **Contradiction resolution is newest-wins, in code.** Superseded rows kept.
- **Episodic memory adds no table** — an episode IS a run, so it is two columns plus queries.
- **The lesson is derived, not generated** — an LLM call per run to restate recorded facts would
  cost ~5% of the daily quota.
- **Conversation memory deliberately not built** — no multi-turn API exists to remember.

## Problems Encountered
1. A flaky database test immediately after starting the container, which stopped reproducing.
2. **The memory tools were never registered.** Demo said "I have noted that"; table was empty.
3. `ForeignKeyViolationError` on every contradiction test.
4. A test asserting an absolute row count broke when a live demo added a real fact.

## How We Solved Them
1. The readiness probe now runs `SELECT 1` and retries, instead of treating "socket opened" as
   "ready". Recorded honestly that the specific failing test was never captured.
2. **My first diagnosis was wrong** — I blamed overlapping tool descriptions. The startup log
   showed four tools instead of seven: a `str.replace` patch had silently no-opped after ruff
   reformatted its target. Added `test_tool_wiring.py` and a fail-loud patcher.
3. Insert-then-update. The FK made supersede-first impossible, and the comment defending that
   ordering was wrong about the danger too — one transaction, so no other transaction sees the
   intermediate state.
4. Assert a delta, not an absolute. Rollback isolates a test's own writes, not the world's.

## Tests Performed
- 350 pass, 2 skipped. Cross-process demo: stated a preference, killed the process, recalled it
  from a new one.
- Migration reverses (`upgrade → downgrade → upgrade`).
- `mypy --strict` and `ruff` clean.

## Things I Learned
- **Unit tests verify components; nothing was verifying they were connected.** 344 tests passed
  with the memory feature completely unreachable. Wiring is a behaviour and needs a test.
- **When a symptom fits "the model did something odd", check the deterministic explanation first.**
  LLM systems make that attribution far too easy, and it is unfalsifiable enough to be comfortable.
  The evidence was one grep away.
- **A silent `str.replace` no-op is a class of bug** — third occurrence. Patching by pattern must
  fail loudly.
- **A confident comment justifying an impossible design is worse than no comment.** Same shape as
  V0.2's `"Verified against the API"`.
- A test over a shared table must measure its own effect, not the table's total.

## Things I Should Investigate
- `_finalise` has been dead for four milestones. Delete it at V0.7.
- Nothing scans a finished run for facts worth remembering — automatic extraction is unbuilt.
- Memories accumulate forever; no eviction story.

## Next Exact Step
V0.7 — Multi-agent. Delete `_finalise` first. Specialised agents need **distinct tool allowlists**,
not just distinct prompts, and routing accuracy must be measured or "specialised" is an unmeasured
claim. Full sequence in `engineering/current-state.md`.

## Session 8 (continued) — V0.7

**Module:** V0.7 — Multi-Agent
**Objective:** Make "multi-agent" an honest word: agents that differ in capability, structured
messages, and a critic gate.

## What We Changed
`src/amos/agents/{registry,messages,router,critic,team,cli}.py`. Deleted `_finalise` (dead four
milestones). Fixed the randomised test fake and the version drift. Wrote
`docs/07-agent-specification.md`, `docs/interview/multi-agent.md`, journal chapter 8. 389 tests.

## Architecture Decisions
- **Specialisation is a tool allowlist enforced by construction**, not a prompt. Disjoint
  allowlists, because overlapping capability makes routing arbitrary.
- **The critic has no tools** — one that can fetch new sources makes its own verdict unfalsifiable.
- **The revise loop is bounded in code**; unresolved objections are attached to the answer with
  confidence downgraded, never hidden.
- **A broken critic accepts** — it is a quality gate, not a correctness requirement.
- **Version defined in `amos.__version__`**, with the build reading it.

## Problems Encountered
1. A flaky retrieval test — passing alone, failing in the suite.
2. `/health` reported the wrong version after the bump.
3. Routing scored 90%, and the miss looked like a router error.
4. `model_copy(update=...)` left a raw string where an enum was annotated.

## How We Solved Them
1. `FakeEmbeddings` used Python's `hash()`, randomised per process. **The existing determinism
   test passed throughout**, because it compared two calls inside one process. Replaced with
   blake2b; the new test shells out to a second interpreter.
2. The version was a literal in three files. First fix — `importlib.metadata` — was a plausible
   inversion that reintroduced staleness, since metadata describes the last *build*. Real fix: one
   definition in code, build derives.
3. **The router was right and my design was wrong.** `recall_past_runs` is a lookup, so it belongs
   to the researcher. Moved it; routing went to 10/10 and the allowlists became disjoint.
4. `model_copy` does not re-validate. Passed the enum member instead of its value.

## Tests Performed
- 389 pass, 2 skipped. Routing measured 10/10 live.
- Demo: a documentation question routed to the researcher, retrieved with citations, critic
  accepted. 4 calls, 3100 tokens.

## Things I Learned
- **A test can certify exactly the property it is missing.** The determinism test measured the
  wrong scope for two milestones.
- **When a fact appears in three places, the fix is one definition** — not discipline about three.
- **A measurement can catch an error in your design rather than the model's behaviour**, which is
  not what I built it to find.
- `model_copy(update=)` bypasses validation — a typed field can silently hold the wrong type.

## Next Exact Step
V0.8 — asynchronous execution. `tasks.claimed_at` and the partial claimable index already exist
from V0.4, so this adds a worker rather than a migration.

## Session 8 (continued) — V0.8

**Module:** V0.8 — Asynchronous Execution
**Objective:** Queue goals to workers; survive a worker crash.

## What We Changed
`src/amos/worker/{queue,runner,__main__}.py`, run-level claim columns, `POST /v1/goals/async`.
Removed V0.4's speculative task-level claim column. Added `test_schema_drift.py`. Wrote
`docs/12-event-system.md`, `docs/interview/async.md`, journal chapter 9. 418 tests.

## Architecture Decisions
- **Claim at run level, not task level.** A run is what a client submits and polls; internal task
  concurrency is already handled inside one worker. This corrected V0.4's speculative column, which
  anticipated the wrong granularity and was **removed** rather than carried.
- **Polling, not `LISTEN`/`NOTIFY`** — a second mechanism (dedicated connection, reconnects, lost
  notifications) to save latency nobody is measuring.
- **At-least-once, stated explicitly.** Exactly-once is unavailable; the mitigation is idempotent
  work, and `remember_fact` is recorded as a real gap rather than a solved problem.
- **The worker swallows every exception**, bounded by an attempt ceiling — a worker that dies on
  one bad run turns a poison message into a total outage.

## Problems Encountered
1. ORM models and the database schema had silently diverged — five columns.

## How We Solved Them
1. Found by diffing `Base.metadata` against `information_schema` before adding new columns, not by
   anything failing. A V0.6 `str.replace` patch had no-opped; nothing broke because the only code
   using those columns goes through raw SQL. Added `test_schema_drift.py` comparing both directions,
   with `vector` columns exempted **by name** so the check keeps its teeth.

## Tests Performed
- 418 pass. Concurrent workers claim disjoint runs; an abandoned run is reclaimed while a healthy
  one is not stolen; the attempt ceiling stops poison messages.
- **Live crash demo:** worker A claimed a run, `kill -9`, worker B swept/reclaimed/completed.
  `attempt_count = 2`, correct answer, different `claimed_by`.
- Async submission measured at **202 in 54 ms** with no LLM call in the request path.

## Things I Learned
- **Migrations and models are two descriptions of one schema, and nothing was comparing them.**
- **A speculative column is worse than a missing one**, because a column in a schema looks like a
  decision someone made for a reason. V0.4's guess was not just unused — it was the wrong shape.
- Crash recovery needs no failure detection. Nothing observes that a worker died, only that a run
  has been held too long. Ten lines of SQL.
- The most valuable paragraphs in `12-event-system.md` are the ones saying what is *not* guaranteed.

## Next Exact Step
V0.9 — observability. The request id threaded since V0.1 becomes the OTel trace id. Watch metric
cardinality, and keep goal text out of span attributes.

## Session 8 (continued) — V0.9 and V1.0

**Modules:** V0.9 Observability, V1.0 Evaluation
**Objective:** Emit standard telemetry; make every quality claim checkable by a command.

## What We Changed
`src/amos/telemetry/{tracing,metrics}.py`, `src/amos/evaluation/**`, `.github/workflows/ci.yml`,
`Makefile`, `otel-collector.yaml`. Wrote `docs/{14,16}`, `docs/interview/{observability,evaluation}.md`,
journal chapters 10 and 11. 480 tests.

## Architecture Decisions
- **Goal text is not a span attribute by default.** Spans are shipped, stored and searchable; the
  safe direction must be the default, because an opt-out ships content from every deployment that
  forgot.
- **Metric labels come from a closed allowlist** — an unbounded label is one time series per value.
- **Deterministic and judged metrics are reported separately, never averaged**, and only the
  deterministic ones gate CI.
- **A rate limit is unmeasurable, not a failure.** Otherwise part of the score measures the free tier.
- CI runs the suite with *and without* a database, migrations in *both* directions, and with **no
  API key** — enforcing N-14 rather than documenting it.

## Problems Encountered
1. A telemetry test leaked the request-id contextvar into unrelated tests.
2. `score = -1` as a sentinel was rejected by the field's own bound.
3. The evaluation suite scored a rate limit as a quality failure.
4. The refusal case scored 0/1 — apparently the worst possible failure.

## How We Solved Them
1. Autouse fixture resetting the contextvar. Autouse, because remembering to clean up global state
   per test is exactly the discipline that fails silently.
2. Replaced with an explicit `judged` flag. A magic number in a numeric field gets averaged by
   accident even when it does construct.
3. Classed as `unmeasurable`, excluded from rates and from the gate. Also surfaced a **fourth quota
   shape**: `flash-lite` is 15/minute where `flash` is 20/day.
4. **Read the output.** The system had refused correctly, in wording the keyword detector did not
   cover. The metric was wrong, not the system.

## Tests Performed
- 480 pass. Live: traces captured by a local collector showing `amos.goal.length` and no goal text.
- `make eval` → 6/6 deterministic, groundedness 1.00, 21252 tokens.

## Things I Learned
- **A crude metric manufactures false failures indistinguishable from real ones until you read the
  output.** I was one paragraph from reporting a brittle detector as a quality finding.
- **A rate limit is not a quality failure**, and conflating them makes every number partly a
  measurement of the free tier.
- Broadening a detector needs a matching test that it still catches the failure it exists for —
  otherwise the fix passes everything.
- `set_tracer_provider` works once per process; later calls are ignored *with a warning*, so a
  per-test provider silently records nothing after the first test.
- The observability milestone was the smallest in the project, because correlation was solved in
  V0.1 by a request id added for debugging.

## Next Exact Step
**The roadmap is complete.** The outstanding work is the advance gate: ten interview documents,
never met. Start with `docs/25-build-journal.md`.

---

# Session 9

**Date:** 2026-09-09 → 2026-09-11
**Module:** Post-V1.0 — memory reliability, version drift, and the build-along guide
**Objective:** Make `remember_fact` actually work, and write the guide for building AMOS by hand.

> **Backfilled 2026-09-13.** This session produced **21 commits** (`v1.0..main`) and was never
> written up. `CLAUDE.md` makes updating this file part of the end-of-session protocol, so the
> omission is a protocol violation visible in `git log` — which is the argument for the protocol.
> Reconstructed from the commits, `bugs-log.md` and `experiments-log.md`, all of which *were*
> kept current.

## What We Changed
- `src/amos/memory/reconcile.py` (new) — guarantees the system never claims to have remembered
  something it did not store.
- `src/amos/agents/registry.py` — `remember_fact` added to the Researcher's allowlist.
- `src/amos/__init__.py`, `src/amos/api/app.py` — version defined once, read everywhere.
- `src/amos/memory/trials.py`, `Makefile` — `make memory-trials`.
- `docs/build-along/` (new, 12 documents) — what file to write, when, why then, and the trap
  waiting in each milestone. Contracts, not code; the git tags are the answer key.
- `docs/25-build-journal.md` chapter 12; `docs/09-memory-architecture.md` reliability figures.

## Problems Encountered
1. `/health` reported version 0.7.0 from a v1.0 build.
2. `remember_fact` was called unreliably — the model said it had remembered things it had not.
3. Three prompt fixes in a row failed to improve the store rate.

## How We Solved Them
1. The version had been a literal in three files. Moved to `amos.__version__`, with the build
   deriving from it via hatchling — the reverse of the original arrangement, which read the
   version recorded at *install* time and so silently ignored every bump.
2. `MemoryReconciler`: compare what the answer *claims* against what was actually written, then
   re-run the tool or flag the caveat. Ground truth is read from the `memories` table, never
   from the model's own account of itself.
3. **The root cause was not a prompt problem at all.** `remember_fact` was registered globally
   and appeared in **no** `AgentSpec` allowlist, so every specialist's filtered registry removed
   it. With routing enabled, storing a memory was *structurally impossible* — and the prompts
   were being tuned to ask for a tool that did not exist in the agent's registry.
   Store rate went 0% → 100% (8/8); false-claim rate 38% → 0%.

## Things I Learned
- **Three failed fixes of the same kind is a signal about the diagnosis, not the fix.** Each
  prompt change was a reasonable idea; the third failure was the evidence that the layer was wrong.
- A capability can be fully built, fully tested, fully documented and completely unreachable.
  The unit tests passed because they constructed the tool directly; nothing tested the wiring.
- A version read at install time is not the version you are running.

## Next Exact Step
Recorded at the time as: the advance gate, still unmet.

---

# Session 10

**Date:** 2026-09-13
**Module:** Coherence audit — code and documentation against the master build prompt
**Objective:** Establish what is built, what is left, and where the documentation has drifted
from the code. Then fix what the audit found.

## What We Changed

**Code — six defects where AMOS disagreed with itself** (`fix/coherence`):
- `httpx` moved to runtime dependencies. It was declared under `[dev]` with the comment
  "required by fastapi.testclient" while `http_get.py` imports it at runtime, so `pip install .`
  produced a package whose only network tool could not import. Nobody saw it because everyone
  installs `-e ".[dev]"`.
- The OpenAPI description read "— V0.7" through the whole of a v1.0 build, served from
  `/openapi.json` and `/docs`.
- `AMOS_ASYNC_ENABLED` was read by nothing while three documents told the reader to set it.
- `retrieval_top_k` and `memory_min_score` were likewise unread.
- `amos.task.retries` was created at V0.9 and never incremented.
- `TaskState.TIMED_OUT` was declared with legal transitions and was unreachable — ADR-009 adds
  the task-level timeout.

**Documentation** — roughly forty discrepancies, in four tiers by how badly a reader is misled.
The worst were: `22-resume-evidence.md` contradicting *itself* about whether "RAG" and
"autonomous" were earned; `07-agent-specification.md` still describing the allowlist from before
the memory bug was fixed; `05-data-model.md` teaching a schema V0.8 deliberately deleted; and
`06-api-specification.md` documenting two of four endpoints at "Current version: V0.1".

**Engineering logs** — `decisions-log.md` had stopped at V0.4 and `learning-log.md` at V0.1;
both backfilled. `23-glossary.md`, the last stub, written.

## Architecture Decisions
- **ADR-009 — a task-level timeout.** The decision is the *ordering* — per-call bounds < task
  timeout < worker visibility timeout — more than the 300s.
- **`AMOS_ASYNC_ENABLED` default stays `false`**, rather than flipping to `true` to preserve the
  current behaviour. The three documents that mention it already assume opt-in, and queueing
  with no worker leaves runs QUEUED forever. Making the documented mental model true was worth
  more than preserving an undocumented one.
- **A test per defect *class*, not per defect.**

## Tests Performed
521 pass (523 collected, 2 live skipped), up from 510. `ruff`, `ruff format` and `mypy --strict`
clean. Measured directly rather than quoted: **451 of 523 pass with no database reachable**,
the other 72 skipping themselves.

## Things I Learned
- **Three of the six defects already had a test nearby that passed.** The version test asserted
  that the *current* version did not appear as a literal, so a stale one sailed through. The
  state-machine tests were exhaustive over the transition *table* — which was correct — while
  nothing produced one of its states. A test that names the bug just fixed protects against a
  bug that has already been fixed.
- **Documentation drifts hardest where it is most confident.** The document that contradicted
  itself was `22-resume-evidence.md`, whose entire purpose is preventing overclaiming.
- **A gap table silently becomes a claim.** `17-failure-recovery.md` promised a dead-letter
  queue "V0.8" and a cost budget "V1.0"; both milestones shipped without them, and nobody
  re-read the table when the milestone arrived.
- Every quota figure in the repo was one of three mutually inconsistent values, because each
  document had learned *one* shape of the limit and written it down as *the* shape.

## Next Exact Step
V1.1 — Reliability: task-level idempotency so a reclaimed run resumes rather than re-executing,
a dead-letter queue, and trace context propagated into workers. ADR first.

## Recommended Commit
Already committed in five parts on `fix/coherence`.

---

# Session 11

**Date:** 2026-09-13
**Module:** V1.1 — Reliability
**Objective:** Make at-least-once delivery honest instead of safe-by-luck: resume a reclaimed run,
collect the runs the queue gives up on, and make a queued run one trace.

## What We Changed
- `src/amos/orchestration/executor.py` — `TaskCheckpoint` protocol; `execute(plan, completed=...)`;
  checkpointing at every terminal transition; `_resumed_result`.
- `src/amos/orchestration/orchestrator.py` — `PlanStore` protocol; `_plan_for` returns the stored
  plan when there is one, and the planner is not called.
- `src/amos/database/progress.py` (new) — both protocols against PostgreSQL.
- `src/amos/database/repository.py` — `_add_task_rows` → `_save_task_rows`, now an upsert.
- `src/amos/worker/queue.py` — `DEAD_LETTER`, `list_dead_letter`, traceparent through the claim,
  `json.dumps` for the give-up reason.
- `src/amos/worker/runner.py` — sets and clears the run id; wraps execution in `continued_trace`.
- `src/amos/telemetry/tracing.py` — `current_trace_context`, `continued_trace`.
- `src/amos/api/app.py` — `GET /v1/runs/dead-letter`, declared before `/v1/runs/{run_id}`.
- `migrations/versions/453890cfd6a9_*` — `runs.trace_parent`.
- Docs: ADR-010, `docs/interview/reliability.md` (new), roadmap V1.1, and `05`, `06`, `12`, `14`,
  `17`, `22` updated to say what is now closed.

## Architecture Decisions
Recorded in `decisions-log.md`. The one that shaped everything: **the stored task rows are the
plan**, because the planner is an LLM and a second call returns a different DAG.

## Problems Encountered
1. Resumption looked impossible at first, for a reason not visible from the API.
2. The first end-to-end test failed, claiming t1 had been re-run when it had not.
3. Two V0.8 tests failed after `give_up` started setting `DEAD_LETTER`.
4. Five telemetry unit tests began failing in the full suite while passing individually.
5. The worker never put the run id in context.

## How We Solved Them
1. **Nothing was persisted until the run finished** — tasks, steps, LLM calls and tool calls all
   landed in one batch in `record_success`. A killed run left a `runs` row and nothing else. The
   milestone is mostly about fixing that, not about the resume logic itself.
2. A test bug, not a code bug: a dependent's goal *contains* its upstream's description as
   context, so `"gather the sources" in goal` matched the t2 goal. `startswith` is the precise
   assertion.
3. Correct failures — the behaviour changed deliberately. Updated both, with a comment saying why.
   **I committed before noticing them**, having run only the files I touched; amended.
4. An integration test called `set_tracer_provider`, which OpenTelemetry honours once per process.
   It ran first and silently blinded every span-capturing test after it. That test now asserts the
   column round-trips and installs no provider.
5. Found while wiring trace propagation, not by a failing test. The synchronous path called
   `set_current_run_id` and the worker path did not — so the plan store and checkpoint would have
   been inert in the worker, the only place a reclaim happens.

## Tests Performed
523 → **551**. `ruff`, `ruff format` and `mypy --strict` clean. Migration applied and reversed and
re-applied. Each new guard verified by reintroducing the bug it exists for: removing
`set_current_run_id` fails the context test; swapping the route order fails the ordering test.

## Things I Learned
- **A capability can be built, tested, documented and wired to nowhere.** Second time in this
  project — `remember_fact` was the first. Unit tests cannot see it, because they construct the
  component and never exercise the entry point.
- **Run the whole suite, not the files you touched.** Two of this session's five problems were
  invisible to a targeted run, and one of them I committed.
- A reliability feature is best described by the window it *narrows*. Resumption takes duplicate
  work from a whole run to a single task; saying "now it's safe" would be false.
- Reading a function while changing something nearby found a real bug (`give_up`'s f-string JSON)
  that no test was ever going to reach, because it needs a quote in an exception message.

## Next Exact Step
**V1.2 — Evaluation credibility.** ADR first. Adversarial cases (prompt injection in the corpus
and in tool output, deliberately misleading entries), a stored baseline so `make eval` compares
against history instead of printing a number and discarding it, and enlarged golden sets — with
the limitation stated plainly, since cases I author are still not independently authored.

## Recommended Commit
Committed in four parts on `feat/v1.1-reliability`.

---

# Session 12

**Date:** 2026-09-13
**Module:** V1.2 — Evaluation credibility
**Objective:** Attack the suite's own stated weakness — self-authored cases, nothing adversarial,
and a score printed then discarded.

## What We Changed
- `tests/unit/rag/test_adversarial_retrieval.py` (new) — a poisoned corpus passage with the model
  fully complying, asserting the registry, the permission refusal, the per-agent allowlist and the
  loop cap all hold.
- `src/amos/evaluation/baseline.py` (new) + `engineering/eval-baseline.json`.
- Golden sets enlarged: goals 6→9, retrieval 12→16, routing 10→15.
- ADR-011; `docs/16-evaluation.md` extended; `13-security.md`, `19-roadmap.md`, `22`.
- `make eval` compares, `make eval-baseline` writes.
- Bumped `__version__` to 1.1.0 — see below.

## Architecture Decisions
**ADR-011 — adversarial cases are tests, not eval cases.** The split follows the existing security
principle: if the claim is "the boundary holds even assuming the model is compromised", a fake
provider that *complies with the attack* is stronger evidence than a real model that might resist,
and it runs in CI for free. Only judgement — refusing when the corpus is misleading — costs quota.

Also: only deterministic metrics gate; a baseline is per-model and per-corpus and reports "not
comparable" rather than a false regression; nothing is written unless asked, because a gate that
updates itself on failure is not a gate.

## Problems Encountered
1. The full suite failed on `test_the_version_matches_the_latest_git_tag`.
2. A claim I had written into `docs/10-rag-architecture.md` three commits earlier was wrong.

## How We Solved Them
1. **I had tagged `v1.1` without bumping `__version__`.** The regression test written for the
   *previous* instance of this bug (2026-09-09) caught it on the very next release. Bumped, and
   recorded in `bugs-log.md` with the lesson: the earlier structural fix made the version live in
   one place, which removed drift *between files* and did nothing about drift between the version
   and the **tag**.
2. I had written "measured when `docs/` held 26 markdown files". Querying the database showed the
   indexed corpus is the 24 numbered docs **plus four interview docs**, and — worse — several of
   those documents have been substantially rewritten since, including two in the audit three
   commits earlier. The count was stale *and* the content was.

## Tests Performed
551 → **567**. `make eval-baseline` run once, deliberately: **9/9, refusal 2/2, groundedness 1.00
(1 judge failure excluded), 40852 tokens.** All three new adversarial cases passed, including the
two expected to be hardest.

## Things I Learned
- **A test written for one instance of a bug caught the next one, in a different form.** The
  structural fix (one definition, build derives from it) was scoped to the mechanism it replaced.
  The test covered the rest, which is the argument for writing one even when the fix feels total.
- Cost scales worse than case count: 6 cases were 21252 tokens, 9 are **40852**. That is most of a
  day's budget on `flash`, and the real reason a golden set cannot simply keep growing — the
  constraint is economic, not methodological.
- **Checking a number I had asserted three commits ago found it wrong.** Written from a plausible
  inference rather than a query, during an audit whose entire subject was claims that had drifted.
- A groundedness mean of 1.00 over *eight of nine* cases is a different fact from 1.00 over nine.
  The harness already reported the excluded failure; writing the number down without it would have
  quietly upgraded the claim.

## Next Exact Step
**V1.3 — Agent-to-agent delegation.** ADR-012 is written; implementation follows.

## Recommended Commit
Committed in three parts on `feat/v1.2-evaluation`.

---

# Session 13

**Date:** 2026-09-13
**Module:** V1.3 — Agent-to-Agent Delegation
**Objective:** Give `AgentTask` a caller, six milestones after it was defined.

## What We Changed
- `src/amos/agents/delegation.py` (new) — the `delegate` tool, `DelegationBudget`, refusal paths.
- `src/amos/agents/team.py` — depth enforced by the tool's absence from the registry; the
  delegation paragraph appended only when the tool is present; the agent list built from the
  registry rather than written into each spec.
- `src/amos/config.py`, `api/dependencies.py`, `.env.example` — three settings.
- ADR-012; `docs/interview/delegation.md` (new); `07-agent-specification.md` extended; roadmap;
  resume evidence.

## Architecture Decisions
In `decisions-log.md`. The one that shaped the rest: **the depth bound is the tool's absence**,
not a check inside it — a check is code that model output flows into.

## Problems Encountered
1. An existing team test asserted the analyst's declared tools were exactly `{calculator}`.
2. The end-to-end test failed on `ToolOutcome.tool_name`.

## How We Solved Them
1. Correct failure — delegation genuinely adds a tool to every routable agent. Updated it, and
   added a second test pinning the invariant it was really protecting: with delegation off, no
   other agent's tools leak in. The original assertion conflated "only its own tools" with "only
   these exact tools".
2. The field is `name`. A five-second fix, worth noting only because it is the kind of thing that
   would have been caught earlier by writing the assertion against the model definition rather
   than from memory.

## Tests Performed
567 → **587**. Most of the new ones are about the bounds rather than the happy path: depth removes
the tool, a cycle terminates, the budget is shared, an exhausted budget is a refusal the caller
can act on, and a delegate does not inherit the caller's tools.

## Things I Learned
- **Specialisation depends on delegation existing.** Without it, the pressure is to widen the
  researcher's allowlist to include `calculator` — and once allowlists overlap, routing accuracy
  stops meaning anything. The feature protects an earlier measurement.
- A bound enforced by **absence** is categorically stronger than one enforced by a check, and the
  difference only shows up under an adversarial assumption.
- Making a prompt depend on a registry that varies at runtime turned a static rule
  ("instructions must match allowlists") into a dynamic one. The rule survived; its implementation
  had to move.

## Next Exact Step
**V1.4 — Authentication and multi-user isolation.** The largest change in the plan: a `users`
table, auth on the API, and `user_id` scoping on runs, memories and documents, enforced in the
repository layer so a query cannot forget it. ADR first, and it must say out loud that
`docs/01-requirements.md` currently lists multi-tenancy as an explicit **non-goal**.

## Recommended Commit
Committed in two parts on `feat/v1.3-delegation`.

---

# Session 14

**Date:** 2026-09-13
**Module:** V1.4 — Authentication and multi-user isolation
**Objective:** Give AMOS a notion of who is asking, and make one user's data unreachable by
another. The largest change in the plan, and deliberately last.

## What We Changed
- `src/amos/auth.py` (new) — hashed API keys, `Actor`, `actor_for_run`, `LOCAL_ACTOR`.
- `src/amos/database/models.py` — `users`; `user_id` on `runs` and `memories` (NOT NULL) and on
  `documents` (nullable — NULL is the shared system corpus).
- `migrations/versions/835121ee2bd2_*` — reversible, with the NOT NULL backfill.
- `repository.py` and `memory/semantic.py` — constructed with their owner; every query scoped.
- `api/app.py` — the auth dependency, 401 before any model call.
- `worker/runner.py` — the worker acts as the run's owner.
- ADR-013; `docs/interview/security.md` (new); `01`, `02`, `05`, `13`, `19`, `22` updated.
- `tests/integration/test_isolation.py` (new) — 23 tests plus two structural guards.

## Architecture Decisions
In `decisions-log.md`. The one that shaped everything: **isolation fails silently**, so
enforcement is at construction rather than per call.

## Problems Encountered
1. Every protected endpoint returned **422** instead of 401.
2. 73 tests failed at once after the constructor signatures changed.
3. Several tests mixed the rollback session with the committing factory and hit FK violations.
4. The no-database API tests all failed: with auth required, they could not authenticate.

## How We Solved Them
1. The auth dependency was defined *inside* `create_app`, so it was a function-local name — and
   `from __future__ import annotations` makes FastAPI resolve the annotation as a **string against
   module globals**, where it did not exist. FastAPI then treated `actor` as an ordinary query
   parameter. Moved to module level. **The symptom looked nothing like the cause.**
2. Expected, and the point: mypy enumerated every call site before a single test ran. Threaded the
   actor through mechanically, using the AST rather than regex after a first attempt mangled
   multi-line signatures.
3. An actor created in a rolled-back session does not exist to a committing one. Added a separate
   `factory_actor` fixture, and used it in exactly the tests that commit.
4. **A real design question, not a test problem.** No database means no users and no isolation to
   enforce, and requiring a key would break "runs without infrastructure" — held since V0.3, with
   its own CI job. Resolved as: unauthenticated in that mode, with a loud startup warning, and
   said plainly in `13-security.md`.

## Tests Performed
589 → **616**. Migration applied, reversed and re-applied; 422 existing runs backfilled, 28
documents left as system corpus. Both structural guards verified by reintroducing the bypass.

## Things I Learned
- **Isolation is the only failure mode in this project that returns more data rather than less.**
  Everything else announces itself — an illegal transition raises, a bad plan is rejected. This one
  looks like a feature working, which is what justifies enforcing it at construction.
- "Security designed in from the start" and "identity built last" are both true here, and the
  distinction is worth being able to state: the controls that constrain the *model* were V0.2 and
  would have been expensive to retrofit; identity is mechanical and scales with the number of
  tables, so doing it once against a settled schema was cheaper.
- Reversing a stated non-goal is fine; reversing it *silently* is what would make the requirements
  document untrustworthy.
- A guard nobody has seen fail is a guard nobody knows works.

## Next Exact Step
**Authorization** is the obvious next one, and the gap most likely to be assumed already solved.
Nothing is committed to; `docs/19-roadmap.md`'s "Beyond V1.4" lists the candidates.

**The advance gate is now twelve documents deep** — `docs/interview/*.md` — and has never been
met. That is the second of the project's two equal objectives and the only one still outstanding.

## Recommended Commit
Committed in two parts on `feat/v1.4-auth`.

---

# Session 15

**Date:** 2026-09-14
**Module:** Housekeeping after V1.4 — attribution, corpus rebuild, and what the rebuild exposed
**Objective:** Strip Claude attribution from history as the working agreement requires; rebuild the
destroyed corpus and re-measure retrieval, routing and evaluation.

## What We Changed
- **History:** `Co-Authored-By: Claude` and `Claude-Session:` lines removed from all 75 commits
  carrying them, across 14 branches and tags `v0.6`–`v1.4.0`, then force-pushed. Every ref's tree and
  commit count was verified identical before and after, so only messages changed. A bundle of the
  pre-rewrite repository was taken first.
- **`rag/ingest.py`:** commits after every document — the V0.5 fix that was recorded and never built.
- **`tests/integration/test_ingest_transactions.py`** (new): two tests, verified to fail without it.
- **`tests/integration/test_trace_api.py`:** a `created_users` fixture; the V1.4 intruder test had
  leaked one user per suite run.
- **Docs:** fifth quota shape; corrections on the never-built fix in `10-rag-architecture.md`, the
  build-along guide and `interview/rag.md`; routing re-measured; `current-state.md` rebuilt around
  an empty corpus.

## Problems Encountered
1. The rebuild's monitor reported a stall after 8 minutes.
2. The rebuild failed — reported as exit 0.
3. A retrieval test failed once during the rebuild.
4. Three stray `intruder-*` users in the database.
5. Two of my own monitoring scripts were wrong.

## How We Solved Them
1. **Not a stall.** The ingest ran in one transaction, invisible to the monitor's connection. That
   led to the real finding: `cli.py` and `ingest.py` each have exactly one version in history, and
   the per-document commit recorded as the 2026-09-05 fix was never written.
2. **`exit 0` was `tail`'s exit code, not Python's.** The traceback showed a *daily* embedding quota
   (1000) the repo had never recorded — and because the process ran pre-fix code, the rollback took
   every document. The 2026-09-05 bug, reproduced live beside its own uncommitted fix.
3. Hypothesised as HNSW losing candidates to uncommitted rows. A deliberate reproduction returned 2
   hits in every mode. Recorded as **unexplained**, not as a known pgvector behaviour.
4. My V1.4 test created a user through the committing factory and never deleted it. Cleanup moved to
   fixture teardown, verified to hold even when the test's assertion fails.
5. One used `&&`/`||` chains that evaluated false while a counter was negative, so it could never
   report progress; another could not see uncommitted rows. Both replaced.

## Tests Performed
**618 passed**, 2 live skipped; ruff, format, mypy clean. User count unchanged across a full suite run.
The ingest tests fail with the fix removed (documents rolled back; nothing skipped on rerun).
`make routing`: **13/15**. `make retrieval` and `make eval-baseline` **not run** — the corpus is
empty and the daily embedding quota is spent.

## Things I Learned
- **A bugs-log "Fix:" line is a claim** and needs a file, a test and a commit like any resume claim.
  This one had none, and propagated into four documents.
- **The rollback test fixture cannot test a transaction boundary** — committed and uncommitted work
  look the same inside it.
- **A pipeline reports its last command's exit status.** `python … | tail` said success while
  Python had crashed. `set -o pipefail`, or read the output, before trusting a zero.
- **A monitor watching a database sees only committed state.** A long transaction is
  indistinguishable from no progress — which here was the bug rather than noise.
- Arithmetic caught a wrong quota claim: under 100 requests cannot exhaust a 1000-request limit.

## Next Exact Step
`make ingest` now and again after the embedding quota resets — the second run resumes. Then
`make retrieval` and `make eval-baseline`, and update the figures they feed.
