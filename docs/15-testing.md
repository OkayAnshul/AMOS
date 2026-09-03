# 15 — Testing

**Written at V0.2**, once there was enough test surface to have real conventions.

## The binding rule

> **No test in the default suite touches the network.**

Requirement N-14. This is not a style preference. The Gemini free tier is **20 requests per
day per model** (`docs/21-technology-baseline.md`), so a suite that called the real API could
exhaust an entire day's budget in a single run — and would be slow, non-deterministic, and
unrunnable in CI.

`FakeProvider` scripts the model's responses instead, including the malformed ones and the
tool-calling ones. That makes the model's behaviour a *fixture*, so tests measure the system's
reaction rather than the model's mood.

One live test exists, opt-in:
```bash
AMOS_RUN_LIVE_TESTS=1 pytest tests/live -v
```

## Layout

```
tests/
├── conftest.py          shared fixtures and valid_response_json()
├── unit/                pure logic, no I/O
│   ├── tools/           per-tool, mostly security
│   └── ...
├── integration/         through the FastAPI app with TestClient
└── live/                the real API, skipped by default
```

## What gets tested, in priority order

1. **Security boundaries.** Path traversal, symlink escape, SSRF, code execution. These are the
   tests where a false pass is a vulnerability, so they assert on *rejection* and on the reason.
2. **Failure paths.** Provider timeouts, malformed output, hallucinated tools, invalid
   arguments, exhausted retries, runaway loops. Two V0.1 bugs lived exclusively in error paths
   that a manual demo never reaches.
3. **Contracts.** Schema validation, status-code mapping, generated tool declarations.
4. **Happy paths.** Genuinely the easiest part and the least likely to break.

## Conventions

- **Test the fake.** `test_fake_provider.py` exists because if test infrastructure lies, every
  test built on it lies too.
- **Fakes over mocks.** `FakeProvider` is a working implementation — it parses, counts tokens,
  advances a script. A mock returning a canned object would exercise almost none of the repair
  loop.
- **Assert the reason, not just the failure.** `assert status is INVALID_ARGS` can pass for the
  wrong reason; also assert the error names the offending field.
- **Parametrize hostile input.** Nine code-execution payloads in one parametrized test is nine
  tests and one function.
- **Name the invariant, not the mechanics.**
  `test_provider_error_is_not_swallowed_by_repair_loop` says why it matters;
  `test_run_2` does not.

## What tests cannot catch

Recorded because V0.1 demonstrated it painfully: a broken editable install left 41 tests passing
while `python -m amos` raised `ModuleNotFoundError` — pytest's own `pythonpath` bypassed the
mechanism that was broken.

**A green suite does not prove the application starts.** Running the demo is part of the
Definition of Done for exactly this reason.

## Commands

```bash
.venv/bin/python -m pytest -q                       # everything, no network
.venv/bin/python -m pytest tests/unit/tools -v      # security tests
.venv/bin/mypy src                                  # strict
.venv/bin/ruff check src tests
AMOS_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live -s   # opt-in
```

## Not yet

Coverage measurement, property-based testing, load testing, CI. CI arrives when there is
something to protect against regression across machines; evaluation tests arrive at V1.0.
