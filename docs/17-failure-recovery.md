# 17 — Failure Recovery

**Written at V0.4.** What AMOS does when things go wrong, and — equally important — what it
deliberately does not do yet.

## Principle

Failure is normal, not exceptional. An agent system calls a non-deterministic model over a
network, runs tools that touch a filesystem and the internet, and does so on a rate-limited free
tier. The question is never whether something fails; it is whether the failure is **contained,
visible and recoverable**.

Three rules follow:

1. **Every failure has a type.** No bare exceptions cross a layer boundary.
2. **Recoverable failures become data.** A tool error is fed back to the model so it can correct
   itself; only unrecoverable failures propagate.
3. **Every bound is enforced in code.** Timeouts, retry budgets, loop caps and task limits are
   guarantees, not requests in a prompt.

## Failure catalogue

| Failure | Detected by | Response | Since |
|---|---|---|---|
| Provider timeout | `asyncio.wait_for` | `ProviderTimeoutError` → 504; task retried | V0.1 |
| Malformed model output | Pydantic validation | bounded repair loop, then `OutputValidationError` → 502 | V0.1 |
| Rate limit (20/day) | HTTP 429 | `ProviderRateLimitError` → 429, carrying the provider's retry delay | V0.1 |
| Invalid API key | HTTP 401/403 | `ProviderAuthError` → 502; missing key fails at **startup** | V0.1 |
| Hallucinated tool name | registry lookup | `not_found` outcome fed back, naming the real tools | V0.2 |
| Invalid tool arguments | schema validation | `invalid_args` fed back **before execution** | V0.2 |
| Tool timeout | per-tool `wait_for` | `timeout` outcome fed back | V0.2 |
| Tool raises | `except Exception` in `Tool.execute` | `error` outcome; the agent loop survives | V0.2 |
| Runaway tool loop | iteration counter | `ToolLoopExhaustedError` → 502 | V0.2 |
| Sandbox / SSRF violation | path and URL checks | `invalid_args`, refused before any I/O | V0.2 |
| Database unavailable | startup connection | fails fast; app runs without persistence if unconfigured | V0.3 |
| Crash mid-run | run row written first | run stays `RECEIVED`, visible as an abandoned attempt | V0.3 |
| Duplicate submission | idempotency key | original run returned; agent not re-run | V0.3 |
| Invalid or cyclic plan | `Plan` validator | rejected pre-persistence; planner re-prompted with the cycle | V0.4 |
| Task failure | executor | bounded retry with jittered backoff, then `PERMANENTLY_FAILED` | V0.4 |
| Dependency failure | executor | dependents `SKIPPED`, transitively | V0.4 |
| Partial success | executor | `PARTIALLY_COMPLETED` — a real outcome, not an error | V0.4 |
| Illegal state transition | state machine | `IllegalTransitionError` — raises, never warns | V0.4 |

## Retry semantics

**What is retried:** task execution failures — a provider timeout, a transient error inside the
agent.

**What is not:** anything a retry cannot fix. A malformed *plan* is re-prompted, not retried
identically. A validation failure is repaired with the error attached. A provider timeout inside
the V0.1 repair loop propagates rather than consuming the repair budget, because re-prompting
cannot fix a network problem.

**Budget:** per task, not per run. One flaky task must not consume its siblings' allowance.

**Backoff:** exponential with full jitter, capped at 30s. Jitter is not decoration — without it,
tasks that fail together retry together, indefinitely.

## Blast radius

A failed task takes down its dependents and nothing else. An unrelated branch of the DAG runs to
completion, and the run reports `PARTIALLY_COMPLETED` with the successful work intact. Tested by
`test_an_unrelated_branch_still_runs_when_another_fails`.

## What is deliberately NOT handled yet

Stating these matters more than the table above — an unlisted gap reads as a claim.

| Gap | Why | When |
|---|---|---|
| **Crash mid-run leaves an abandoned `RECEIVED` run** | Nothing sweeps them. The row is evidence, but no process resumes or fails it. | V0.8 — visibility timeout on `claimed_at` |
| **No resumption.** A restarted process does not continue an interrupted run | Requires a worker that claims work, not an HTTP handler that holds it | V0.8 |
| **Tasks are not idempotent.** A retried task re-executes fully | Safe today because every tool is read-only. **This becomes a real bug the moment a `WRITE` tool exists** — which is why the registry refuses to register one | before any write tool |
| **No re-planning.** A failed task is retried as written, not re-approached | The planner runs once per goal | future |
| **No circuit breaker.** Repeated provider failures keep being attempted | Retry budgets bound it; a breaker would bound it sooner | if it becomes a problem |
| **No dead-letter queue** for permanently failed work | Failures are recorded, not queued for later attention | V0.8 |
| **No per-run cost budget.** Retries are bounded by count, not by tokens spent | Count is a proxy; a budget would be the real thing | V1.0 |

## Exactly-once, and why it is not on offer

AMOS does not provide exactly-once execution and will not claim to. Once V0.8 adds a worker, the
guarantee available is **at-least-once**: a worker can complete a task and die before recording
it, and the task will be retried.

The correct response is idempotent tasks, not a stronger delivery promise — which is why the row
above is flagged as a real gap rather than a theoretical one.

## Recovery in practice

For a run that behaved unexpectedly:

```bash
curl -s localhost:8000/v1/runs/$RUN_ID | jq
```

The trace carries every task and its final state, attempt counts (so a retry is visible), every
LLM call with tokens and latency, every tool call with arguments and result, and the error on
whatever failed. That is what V0.3 existed to make possible, and what makes this document
actionable rather than aspirational.
