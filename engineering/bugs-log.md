# Bugs Log

Every non-trivial bug: symptom, cause, fix, and the lesson. The lesson is the reason this file
exists — a bug that teaches nothing was a typo, and a bug fixed without understanding will
return.

**Format:**
```
## YYYY-MM-DD — One-line symptom
Milestone:
Symptom:            what was observed
Expected:           what should have happened
Root cause:         the actual cause, not the first plausible one
Fix:                what changed, and where
How it was found:   test / demo / runtime — and if not a test, why no test caught it
Lesson:             what prevents the whole class of this bug
Test added:         the regression test name
```

---

## No bugs yet

Phase 0 produced no code. The first entry will arrive during V0.1.

**Near-miss worth recording** — 2026-09-03: `gemini-embedding-001` outputs 3072 dimensions by
default, and pgvector's HNSW index supports at most 2000. Caught during Phase 0 by verifying
against the pgvector README rather than assuming.

Had it not been caught, it would have surfaced at V0.5 as either a silent full-scan on every
query, or an index-creation failure after the entire corpus was already embedded — requiring
re-embedding everything. Recorded in ADR-008.

*Lesson: verifying a numeric limit before designing around it costs minutes; discovering it
after building on the assumption costs the corpus.*

---

## 2026-09-03 — `git push` hangs indefinitely
**Milestone:** Phase 0
**Symptom:** `git push` and `ssh -T git@github.com` both hang until timeout. No error.
**Expected:** push completes, or fails fast with a clear error.
**Root cause:** outbound TCP port 22 is blocked on this network (campus filtering).
`github.com` was already in `known_hosts`, so it was not a host-key prompt.
**Fix:** remote switched to SSH over port 443, which GitHub serves at `ssh.github.com`:
```
git remote set-url origin ssh://git@ssh.github.com:443/OkayAnshul/AMOS.git
```
**How it was found:** isolated the layer — `curl https://github.com` returned 200 while SSH
timed out, proving the network was up and the block was port-specific.
**Lesson:** a hang is not an authentication failure. Test each layer separately (DNS → TCP port
→ auth) instead of assuming the topmost one. HTTPS with the `gh` token is the alternative fix.
**Test added:** none — environmental, not a code defect. Recorded in `current-state.md` so a
future session on a different network does not rediscover it.

---

## 2026-09-03 — `TypeError: log_event() got multiple values for argument 'message'`
**Milestone:** V0.1
**Symptom:** every API error path returned a `TypeError` instead of the intended error envelope.
Three integration tests failed.
**Expected:** the handler logs the error and returns a JSON envelope with the right status.
**Root cause:** `log_event(logger, message, **fields)` takes `message` positionally. The error
handler called it with `message=exc.message` as a keyword, colliding with the positional
parameter. A signature collision, not a logic error.
**Fix:** renamed the structured field to `error_message` (`src/amos/api/app.py`).
**How it was found:** `tests/integration/test_api.py` — the error-path tests. Notably the happy
path was unaffected, so **manual testing would have missed this entirely**: the bug lived only
in the paths a human demo never exercises.
**Lesson:** a `**kwargs` logging helper silently creates collisions with its own positional
parameters. Prefix-namespacing structured fields (`error_message`, not `message`) avoids the
whole class. Test error paths as deliberately as happy paths.
**Test added:** already existed — `test_provider_errors_map_to_correct_status`,
`test_unrepairable_output_returns_502`.

---

## 2026-09-03 — `python -m amos` fails with ModuleNotFoundError despite pip reporting it installed
**Milestone:** V0.1
**Symptom:** `pip show amos` reported version 0.1.0 installed; `import amos` raised
`ModuleNotFoundError`. Tests passed the whole time.
**Expected:** an editable install makes the package importable.
**Root cause:** with a `src/` layout, hatchling needs an explicit editable target. Only
`[tool.hatch.build.targets.wheel]` was configured, so the editable install produced dist-info
metadata but no path hook. Tests passed because pytest was using
`pythonpath = ["src"]` from `pyproject.toml` — masking the broken install completely.
**Fix:**
```toml
[tool.hatch.build.targets.editable]
dev-mode-dirs = ["src"]
```
**How it was found:** running the app for the demo. **The test suite could never have caught
it** — pytest's own `pythonpath` bypassed the mechanism that was broken.
**Lesson:** a green test suite does not prove the application starts. Definition of Done
requires *running the app*, not only running its tests, precisely because the two can use
different import paths.
**Test added:** none. The right check is the demo step in the Definition of Done, which is what
caught it.

---

## 2026-09-03 — 400 INVALID_ARGUMENT: "Function call is missing a thought_signature"
**Milestone:** V0.2
**Symptom:** the first live tool-loop test failed on the *second* round trip. Single-turn tool
calls worked; sending the model's tool call back with a result was rejected.
**Expected:** the conversation continues and the model answers using the tool result.
**Root cause:** Gemini 3.x attaches an encrypted `thought_signature` to function-call parts and
requires it returned **verbatim**. AMOS's provider-agnostic `Turn` reconstructed the call from
`ToolCall(id, name, arguments)` — semantically identical, signature absent. The abstraction was
lossy in a way the API treats as fatal.
**Fix:** `Turn.provider_state` — an opaque field holding the vendor's original content object,
which only the producing provider interprets. `_to_contents` replays it verbatim for model
turns. The agent loop never reads it.
**How it was found:** the live tool test. **Unit tests could not have caught this** — the fake
provider has no signatures to lose, so every scripted test passed.
**Lesson:** a provider-agnostic abstraction will eventually meet vendor state that cannot be
represented generically. The answer is an explicit opaque escape hatch, not a leaky
approximation — and not pretending the state does not exist. ADR-005 predicted this ("provider-
specific features need explicit escape hatches"); it arrived one milestone later.
Second lesson: **fakes cannot test what they do not model.** Some things only the live API
reveals, which is the argument for keeping a live smoke test even when it must be opt-in.
**Test added:** `test_real_gemini_uses_the_calculator_tool` (live, opt-in).

---

## 2026-09-03 — Wasted API call per goal from an unverified "verified" claim
**Milestone:** V0.2
**Symptom:** every tool-using goal cost 3 LLM calls where 2 would do.
**Expected:** 2.
**Root cause:** `_finalise()` existed because its docstring stated Gemini "does not accept a
response schema and tool declarations in the same request", annotated *"Verified against the
API — the constraint is real, not a design preference."* **It had never been tested.** The
combination is accepted. Requesting the schema on every turn means a turn that stops calling
tools already carries the validated answer.
**Fix:** pass `response_schema=AgentResponse` alongside `tools` on every loop iteration;
`_finalise` demoted to a fallback for when the model returns something unparseable.
**How it was found:** investigating the free-tier quota. With 20 requests/day, a wasted call
per goal is a third of the budget — which is what made it worth checking at all.
**Lesson:** the damage was not the wrong belief, it was **writing it down as verified**. A
confident annotation stops the next reader — including the author — from questioning it. Do not
write "verified" for something assumed; say "assumed, not tested" and it will get checked.
**Test added:** `test_no_extra_call_when_the_tool_turn_returns_a_valid_answer`,
`test_schema_is_requested_alongside_tools`.

---

## 2026-09-03 — postgres:18 container exits(1) immediately
**Milestone:** V0.3
**Symptom:** `podman-compose up -d` reported success; the container was `Exited (1)` seconds
later. `pg_isready` failed with "container state improper".
**Expected:** PostgreSQL starts and accepts connections.
**Root cause:** PostgreSQL **18** images changed the data-directory convention. The volume must
mount at `/var/lib/postgresql`, not `/var/lib/postgresql/data` — the image now places data in a
version-named subdirectory so `pg_upgrade --link` works across one mount boundary. The old path
is recognised and refused.
**Fix:** `- amos-pgdata:/var/lib/postgresql` in `compose.yaml`; removed the stale volume.
**How it was found:** `podman logs amos-postgres`. The image explains the problem clearly — but
only in its logs, and `compose up` reported success, so nothing pointed at them.
**Lesson:** "the container started" is not "the service is running". A healthcheck belongs in the
compose file from the first version, and **read the logs of a container that exits, rather than
trusting the orchestrator's exit code.** Also: a widely-copied compose snippet can be silently
stale — this path was correct for postgres:17 and every tutorial still shows it.
**Test added:** none — infrastructure config. The healthcheck in `compose.yaml` now surfaces it.

---

## 2026-09-03 — "attached to a different loop" in database tests
**Milestone:** V0.3
**Symptom:** 10 of 11 persistence tests failed with
`RuntimeError: got Future attached to a different loop` and
`InterfaceError: another operation is in progress`. The first test passed; the rest did not.
**Expected:** all tests pass against a real database.
**Root cause:** the engine fixture was `scope="session"`, but pytest-asyncio gives each test its
own event loop. asyncpg connections are bound to the loop that created them, so every test after
the first received a connection belonging to a dead loop.
**Fix:** function-scoped engine with `NullPool`, so nothing is carried between tests.
**How it was found:** the failure pattern — first test passes, all later ones fail — points at
shared state across tests rather than at the code under test.
**Lesson:** **async fixtures must not outlive the event loop they were created in.** Fixture
scope and event-loop scope are separate settings and it is easy to make them disagree. When the
first test passes and the rest fail identically, suspect the fixtures, not the subject.
**Test added:** the whole persistence suite now passes; the fixture carries a comment explaining
why it is function-scoped, so nobody "optimises" it back.

---

## 2026-09-03 — Tests connected to a real database because they read the developer's `.env`
**Milestone:** V0.3
**Symptom:** adding `AMOS_DATABASE_URL` to `.env` broke 12 previously-passing integration tests,
which began attempting real network connections.
**Expected:** tests are unaffected by a developer's local environment.
**Root cause:** `Settings` declares `env_file=".env"`, so `Settings(gemini_api_key="test-key")`
in a test still loaded every other value from the real `.env` — including the new database URL,
which switched persistence on inside tests that had no database.
**Fix:** `isolated_settings()` in `tests/conftest.py`, constructing `Settings(_env_file=None, …)`.
**How it was found:** tests that had passed for two milestones failed after a change to a file
that is not in the repository.
**Lesson:** **a test that reads `.env` is a test that depends on the machine it runs on.** It had
been latent since V0.1 and only surfaced when `.env` gained a setting that changed behaviour. Any
config object with a file source needs an explicit test-time escape hatch from day one.
**Test added:** every integration test now builds settings through `isolated_settings()`.

---

## 2026-09-05 — Migration applied but could not be reversed
**Milestone:** V0.4
**Symptom:** `alembic upgrade head` succeeded; `alembic downgrade -1` failed with
`CompileError: Can't emit DROP CONSTRAINT for constraint ForeignKeyConstraint(...); it has no name`.
**Expected:** every migration reverses.
**Root cause:** SQLAlchemy let the database invent the foreign key's name, so the autogenerated
downgrade had no name to drop it by. Nothing was wrong with the upgrade — the defect was
invisible until the reverse was attempted.
**Fix:** a `naming_convention` on `Base.metadata` giving every index, constraint and key a
deterministic name derived from its table and columns. Regenerated the migration.
**How it was found:** deliberately testing `downgrade` as part of the Definition of Done. A
migration that is only ever applied forwards looks perfect.
**Lesson:** **an irreversible migration is a one-way door, and you discover it at the worst
possible moment.** Test `downgrade` on every migration, not just `upgrade`. Set a metadata
naming convention on day one — retrofitting it means rebaselining.
**Follow-on decision, recorded because it is the kind of thing that bites in a team:** the fix
required a fresh baseline, so V0.3's migration was squashed into a single V0.4 baseline. That is
a **history rewrite of a shipped artifact** and is only safe because that migration had ever run
on exactly one machine. Once anyone else has applied a migration, this option is gone and the
only route is a new forward migration.
**Test added:** none automated yet — `alembic downgrade base && alembic upgrade head` is run
manually before each migration is committed. Worth automating at V1.0 CI.

---

## 2026-09-05 — Rate-limited ingest discarded all its work
**Milestone:** V0.5
**Symptom:** ingesting 300 chunks hit a 429 partway through and raised. `documents` and `chunks`
were both empty afterwards — every successfully embedded chunk was lost.
**Expected:** work already done survives a later failure.
**Root cause:** `ingest_directory` ran the whole corpus inside **one** `session_scope`, so the
transaction rolled back everything. The rollback was correct behaviour; the transaction boundary
was wrong. Compounding it, the embedding quota turned out to be per-minute and content-counted,
so a large ingest hitting a limit was near-certain rather than unlikely.
**Fix:** one transaction **per document**, plus paced batches and retry honouring the provider's
`retryDelay`.
**How it was found:** the first real ingest of a real corpus. No unit test would have caught it —
`FakeEmbeddings` never rate-limits, and a 3-document fixture never runs long enough to fail
partway.
**Lesson:** **transaction boundaries should follow units of useful work, not units of code.** All
300 chunks in one transaction reads as "atomic ingestion" and is actually "lose everything on any
failure". Ask what the caller wants to keep when it fails halfway — here, every document already
finished. Corollary: a long-running loop over an external API needs its failure behaviour designed,
not inherited from whatever `with` block happens to enclose it.
**Test added:** `test_reingesting_unchanged_content_is_a_noop` covers the hash path; the
transaction boundary is exercised by the real ingest, which is honest about the limits of fakes.

---

## 2026-09-09 — One database test failed immediately after starting the container
**Milestone:** between V0.5 and V0.6
**Symptom:** `pytest` run seconds after `podman start amos-postgres` reported `1 failed`. A
re-run passed. The specific test was not captured before it stopped reproducing.
**Expected:** either the suite passes, or the database tests skip.
**Root cause:** not conclusively identified — recorded honestly as such. The likely cause is that
`_database_reachable()` opened a connection to a container still initialising: Postgres accepts
connections slightly before it is ready to serve, so the probe succeeded and a later statement in
the test failed.
**Fix:** the probe now executes `SELECT 1` rather than only connecting, and retries up to five
times with a 1s gap. A cold container is now waited for rather than reported as absent.
**How it was found:** the start-of-session check that the system still runs before building on
it — the reason that step exists.
**Lesson:** **a readiness probe must exercise the thing you actually need, not the nearest cheap
proxy.** "Can I open a socket" is not "can I run a query", the same way "the container started"
is not "the service is running" (V0.3's postgres:18 bug — the second time this shape has appeared).
Also: when a flaky failure stops reproducing before you capture it, say so. A confident
post-hoc diagnosis of a test you never saw fail is a guess wearing evidence's clothes.
**Test added:** none — this is test infrastructure. The retry is the fix.

---

## 2026-09-09 — Memory tools were built, tested, documented, and never registered
**Milestone:** V0.6
**Symptom:** the first cross-session demo failed. Session 1 answered *"I have noted that your
preferred backend language is Python"* — and the `memories` table was empty. Session 2 reached for
`search_knowledge` instead of `recall_facts`.
**Expected:** `remember_fact` is called, the fact persists, a later process recalls it.
**Root cause:** two layers, and the first diagnosis was wrong.

*First (incorrect) reading:* "the model chose badly; the tool descriptions overlap." Plausible, and
it fit the symptom.

*Actual cause:* the tools were never offered. The startup log showed
`["calculator", "http_get", "read_file", "search_knowledge"]` — four tools, not seven. A
`str.replace` patch to `build_registry` had **silently failed to apply** because ruff had
reformatted the signature the pattern matched against. The import patch failed the same way. The
model could not have called a tool it was never given.
**Fix:** rewrote `build_registry` with a patcher that exits non-zero when its pattern is absent,
and added `tests/unit/test_tool_wiring.py` asserting exactly which tools the agent receives under
each configuration.
**How it was found:** reading the startup log line that lists registered tools, instead of
theorising about model behaviour. The evidence was one grep away from a diagnosis that would have
been wrong.
**Lessons:**
1. **Unit tests verify components; nothing was verifying they were connected.** 344 tests passed
   with the feature entirely unreachable. Wiring is a behaviour and needs its own test.
2. **A silent `str.replace` no-op is a whole class of bug** — the third occurrence in this project.
   Editing by pattern match must fail loudly when the pattern is missing.
3. **When a symptom is consistent with "the model did something odd", check the deterministic
   explanation first.** LLM systems make it far too easy to attribute a plain wiring bug to model
   behaviour, and that explanation is unfalsifiable enough to be comfortable.
**Test added:** `tests/unit/test_tool_wiring.py` (6 tests).

---

## 2026-09-09 — Foreign key rejected the supersession ordering the comment defended
**Milestone:** V0.6
**Symptom:** `ForeignKeyViolationError: Key (superseded_by)=(...) is not present in table
"memories"` on every contradiction test.
**Root cause:** `remember()` marked the old row `superseded_by = new_id` **before** inserting the
new row — so the FK pointed at a row that did not exist yet. The code carried a comment explaining
that this ordering was necessary to avoid briefly having two current facts.
**Fix:** insert first, then update, excluding the new row (`AND id <> :new_id`, or it supersedes
itself and the subject ends up with *zero* current facts).
**Lesson:** the comment was wrong twice — the ordering it defended was impossible, and the danger
it warned about was not real, since both statements run in one transaction and no other transaction
observes the intermediate state. **A confident comment justifying an impossible design is worse
than no comment**, because it discourages the next reader from checking. Same shape as V0.2's
`"Verified against the API"` docstring that had never been verified.
**Test added:** the whole contradiction-resolution suite.

---

## 2026-09-09 — A test asserted an absolute count over a shared table
**Milestone:** V0.6
**Symptom:** `test_count_reports_only_current_facts` passed, then failed after a live demo stored
one real fact.
**Root cause:** the test asserted `count_current() == 2`. The rollback fixture isolates a test's
*own* writes; it does not remove rows other things committed.
**Fix:** assert a delta (`before + 2`).
**Lesson:** **a test over a shared table must measure its own effect, not the table's total.**
Transactional isolation makes tests independent of each other, not independent of the world.

---

## 2026-09-09 — A "deterministic" test fake was randomised per process
**Milestone:** V0.7 (introduced at V0.5)
**Symptom:** `test_relevant_passage_ranks_first` failed in a full-suite run and passed in
isolation, then passed on re-run. Classic flake.
**Root cause:** `FakeEmbeddings` built its vectors with Python's built-in `hash()`, which is
**randomised per process** by PYTHONHASHSEED. Every run produced different embeddings, so whether
the relevant chunk ranked first depended on the seed. Its docstring said "deterministic".
**Fix:** `blake2b` — stable across processes and Python versions.
**How it was found:** noticing that a test which passes alone and fails in a suite is usually
shared state or nondeterminism, then checking `hash()` across three interpreters.
**Lesson — the interesting half:** `test_fake_embeddings_are_deterministic` **existed and passed
throughout**, because it compared two calls *inside one process*, where `hash()` is perfectly
stable. The test measured the wrong scope, so it certified exactly the property it was missing.

**Testing determinism requires comparing against a value fixed OUTSIDE the process.** The
replacement test shells out to a second interpreter and pins the result.

More generally: a test that can only observe one process cannot detect process-level
nondeterminism, and its name will confidently claim otherwise. The V0.5 flakiness was latent for
two milestones behind a green test.
**Test added:** `test_fake_embeddings_are_stable_ACROSS_processes`.

---

## 2026-09-09 — The version was a literal in three files and drifted
**Milestone:** V0.7
**Symptom:** `test_health_reports_version` failed after the V0.7 bump. `/health` reported `0.6.0`
while `pyproject.toml` said `0.7.0`.
**Root cause:** the version existed as a literal in `pyproject.toml`, `FastAPI(version=...)` and
the `/health` body. A patch script updated two of them and aborted before the third, which is a
symptom rather than the cause — three copies of one fact will drift eventually regardless.
**First fix, which was wrong:** `importlib.metadata.version("amos")`. That reads the version
recorded at **install** time; in an editable install it is whatever `pip install -e` last saw, so
bumping pyproject changed nothing and `/health` then reported `0.3.0` — stale in a new way, and
harder to notice.
**Actual fix:** `__version__` defined in `src/amos/__init__.py`, with
`[tool.hatch.version] path = "src/amos/__init__.py"` so the build reads it from the code. One
definition; everything else derives.
**Lesson:** **when a fact appears in three places, the fix is one definition — not better
discipline about updating three.** And "read it from the package metadata" is a plausible-looking
inversion that reintroduces staleness through a longer path: metadata is a build artefact, so it
describes the last build, not the source.
**Test added:** `test_the_version_is_defined_in_exactly_one_place` asserts no version literal
returns to `app.py` or `pyproject.toml`.

---

## 2026-09-09 — ORM models and database schema had silently diverged
**Milestone:** V0.8 (introduced at V0.6)
**Symptom:** none. Everything worked. Found while adding V0.8's claim columns.
**Root cause:** `runs.lesson` had been added to the database by a hand-written V0.6 migration while
the matching `str.replace` patch to `models.py` **silently failed** — the same class of bug as the
unregistered memory tools. Five columns were in the database and absent from the model.

Nothing broke because `amos.memory.episodic` reads and writes those columns through **raw SQL**,
so the missing ORM attribute was never accessed. The drift was invisible precisely because the one
code path that used the column did not go through the model.
**Fix:** synchronised the models, and added `tests/integration/test_schema_drift.py` comparing
`Base.metadata` against `information_schema` for every table, in both directions.
**How it was found:** writing a one-off script to diff the model against the live schema before
adding new columns — not by anything failing.
**Lessons:**
1. **Migrations and models are two descriptions of one schema, and nothing was comparing them.**
   Hand-written migrations make that drift possible; autogenerate would have caught it, but
   autogenerate cannot express `CREATE EXTENSION` or a `vector` column, so some migrations must be
   hand-written. The answer is a drift test, not avoiding hand-written migrations.
2. **A silent patch failure is now the third distinct bug of that shape** (V0.6 tools, V0.6
   `build_registry`, this). Every one was invisible to the test suite.
3. The exemption list is explicit rather than a blanket "ignore mismatches" — `vector` columns are
   named individually, so the check keeps its teeth.
**Test added:** `test_schema_drift.py` (9 tests, parametrised per table).

---

## 2026-09-09 — `/health` reported version 0.7.0 from a v1.0 build
**Milestone:** found by end-to-end verification after V1.0
**Symptom:** `curl localhost:8000/health` returned `{"status":"ok","version":"0.7.0"}` on a tree
tagged `v1.0`.
**Root cause:** `__version__` in `src/amos/__init__.py` was never bumped for V0.8, V0.9 or V1.0.
The V0.7 fix had correctly made it the *single source* of truth — and then nobody updated the
source.
**Why nothing caught it:** `test_the_version_is_defined_in_exactly_one_place` asserted the version
was not duplicated. It never asserted the value was **right**. And the API test hardcoded
`"0.7.0"`, so it passed by agreeing with the bug — the very duplication the V0.7 fix removed,
reintroduced in the test suite.
**Fix:** bumped to 1.0.0; the API test now reads `__version__` instead of repeating it; added
`test_the_version_matches_the_latest_git_tag`.
**How it was found:** running the app and reading the health response during end-to-end
verification. Not by any test.
**Lesson:** **"defined in one place" is a weaker property than "correct".** A single-source test
proves there is one copy, not that the copy is current — and a second assertion that hardcodes the
value passes by matching the mistake. Tie the check to something that changes for an independent
reason (here, the git tag).
Also, again: **a green suite does not prove the application behaves correctly** — the same lesson
as V0.1's broken editable install, in a new place.
**Test added:** `test_the_version_matches_the_latest_git_tag`.

---

## 2026-09-09 — `remember_fact` is called unreliably
**Milestone:** found by end-to-end verification after V1.0
**Symptom:** *"Remember for later sessions: my final year project is called AMOS and my mentor is
Dr Sharma"* produced a call to **`recall_facts`**, not `remember_fact`. Nothing was stored, and the
model replied as though it had been. A near-identical goal moments later
(*"Remember this for future sessions: my mentor is Dr Sharma"*) called `remember_fact` correctly
and stored the row.
**Expected:** a goal stating a durable fact reliably stores it.
**Root cause:** model tool selection. This is the *second* occurrence of the same shape — V0.6
fixed a case where the tools were never registered at all, and sharpened the two descriptions.
That was a real bug; **this is a different one underneath it, which the first fix masked.** The
descriptions are now disjoint and the system prompt says explicitly not to claim a fact was noted
without calling the tool, and the model still sometimes reaches for recall when asked to remember.
**Status: NOT FIXED.** Recorded rather than papered over.
**Why prompting is the wrong fix:** three attempts have now gone into instructions. AMOS's own
governing rule says the answer — *"LLMs handle uncertainty; software handles guarantees"*
(`docs/02-system-architecture.md`). If storing a stated fact must be reliable, it cannot be left to
tool selection. Options, none yet chosen:
  1. A deterministic post-run step that extracts stated facts and writes them — code decides, not
     the model.
  2. A critic-style check: if the answer claims something was remembered, verify a write happened,
     and fail the run if not. Turns a silent lie into a visible error.
  3. Accept it, and stop having the model *claim* it remembered — the worst property here is not
     the missed write, it is confidently reporting a write that did not occur.
**Lesson:** **a fix that makes a symptom rarer can hide a second cause.** V0.6's wiring bug was
real and its fix was correct, and it also stopped the underlying unreliability from being visible.
When a bug has two causes, fixing the loud one buys silence, not correctness.
**Test added:** none yet — a non-deterministic failure needs a repeated-trial harness to measure a
rate, not a single assertion. Logged as the honest next piece of work.
