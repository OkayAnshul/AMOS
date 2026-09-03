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
