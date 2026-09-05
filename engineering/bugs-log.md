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
