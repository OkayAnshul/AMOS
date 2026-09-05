# 22 — Resume Evidence

Every claim AMOS may make, with the evidence backing it. **A row without a file, a test and a
demo does not go on a resume.**

## Rules

1. Nothing is added here until the milestone ships and its tests pass.
2. Every claim names the file, the test and the demo that prove it.
3. If it cannot be explained at the **Defend** level (`20-learning-roadmap.md`), it is cut —
   an unexplainable resume line is worse than an absent one.
4. Numbers are measured, never estimated. No "40% faster" without a before and after.

## Current status

**V0.4 shipped.** Four claims are evidenced. Everything below them remains unbuilt and unclaimable.

## Evidence table

| Claim | Milestone | Files | Tests | Demo | Status |
|---|---|---|---|---|---|
| Typed LLM service with provider abstraction and validated structured output | V0.1 | `src/amos/llm/base.py`, `src/amos/agents/agent.py`, `src/amos/api/app.py` | 41 tests, `tests/unit/test_agent_repair.py` | `POST /v1/goals` | ✅ **shipped** |
| Tool-using agent with schema-validated arguments, timeouts and permission allowlists | V0.2 | `src/amos/tools/**`, `src/amos/agents/tool_agent.py` | 76 tool/agent tests | `POST /v1/goals` with a tool-requiring goal | ✅ **shipped** |
| Durable execution history with full request tracing and idempotent submission | V0.3 | `src/amos/database/**`, `src/amos/api/persistence.py` | 18 DB integration tests | `GET /v1/runs/{id}` | ✅ **shipped** |
| Goal decomposition into a persisted task DAG with enforced state machine and bounded retries | V0.4 | `src/amos/orchestration/**` | 130 orchestration tests | 3-task DAG demo | ✅ **shipped** |
| Retrieval pipeline with citation grounding and measured recall@k | V0.5 | — | — | — | ⬜ not built |
| Tiered memory: semantic, episodic and working stores | V0.6 | — | — | — | ⬜ not built |
| Multi-agent orchestration with structured contracts and a critic gate | V0.7 | — | — | — | ⬜ not built |
| Asynchronous execution with crash-safe job claiming via `SKIP LOCKED` | V0.8 | — | — | — | ⬜ not built |
| Distributed tracing with OpenTelemetry | V0.9 | — | — | — | ⬜ not built |
| Evaluation harness gating regressions in CI | V1.0 | — | — | — | ⬜ not built |

## Words that must never be used unless earned

| Word | Requires | Currently |
|---|---|---|
| "multi-agent" | ≥2 genuinely specialised agents collaborating | ❌ zero agents |
| "RAG" | a real pipeline with measured retrieval quality | ❌ not built |
| "autonomous" | the system decides, not a human | ❌ nothing runs |
| "distributed system" | actually distributed — AMOS is a modular monolith | ❌ never, per ADR-004 |
| "production" | deployed, monitored, used by someone | ❌ not deployed |
| "Kubernetes" / "Kafka" / "microservices" | actually used | ❌ deliberately not used |
| "scalable" | a measurement under load | ❌ no load test exists |

Some of these will never become true, by design. That is a feature of the plan, not a gap in it
— ADR-004 rules out "distributed system" permanently, and the roadmap contains no Kubernetes.

## Per-milestone template

Filled in as each milestone ships:

```
### Feature
What was implemented:
Evidence (files):
Tests (names, and what they prove):
Demo (exact command and expected output):
Technical explanation (3-4 sentences, unaided):
Likely interview questions:
Honest resume wording:
What this does NOT demonstrate:
```

The last line is the one that keeps the rest honest.


---

### V0.1 — Grounded Agent API

**What was implemented:** A FastAPI service that turns a natural-language goal into a
schema-validated structured response, behind a provider protocol, with bounded recovery from
malformed model output and typed errors mapped to HTTP status codes.

**Evidence (files):**
- `src/amos/llm/base.py` — `LLMProvider` protocol, `LLMRequest`/`LLMResponse`/`LLMCallRecord`
- `src/amos/llm/gemini.py` — vendor errors translated to typed errors at the boundary
- `src/amos/agents/agent.py` — the validate-and-repair loop
- `src/amos/api/app.py` — error→status mapping table, request-id middleware
- `src/amos/config.py` — 12-factor config, fails fast on missing key

**Tests (41, and what they prove):**
- `test_agent_repair.py` — malformed JSON repairs once; schema violation repairs; repairs
  exhaust into a typed error; a **provider timeout is not swallowed** by the repair loop;
  tokens accumulate across wasted attempts
- `test_api.py` — status mapping (422/429/502/504), request-id echo, repair visible to caller
- `test_fake_provider.py` — the test double itself is tested
- All 41 run without network access

**Demo:** `.venv/bin/python -m amos`, then
`curl -X POST localhost:8000/v1/goals -d '{"goal":"..."}'`.
Verified live: 362 tokens, ~16s, `repair_count=0`.

**Technical explanation (unaided):** The agent asks Gemini for a response conforming to a
Pydantic schema. Provider-enforced schemas make valid output likely but not guaranteed —
truncation, safety stops and unenforced constraints all produce unusable output — so the result
is validated locally and, on failure, re-prompted with the specific error, bounded by a retry
limit. `LLMProvider` is a Protocol rather than an ABC because structural typing lets the test
fake substitute without inheritance, and that fake is required anyway since tests must not hit a
15 RPM API. The `AgentResult` envelope is deliberately shaped like the database rows it becomes
at V0.3.

**Likely interview questions:** in `docs/interview/foundation.md`.

**Honest resume wording:**
> Built a typed LLM service in Python 3.14 / FastAPI with a provider-abstraction layer,
> schema-validated structured outputs, and bounded recovery from malformed model responses;
> 41 tests running without network access via a scripted fake provider.

**What this does NOT demonstrate:** no persistence, no concurrency, nothing distributed. One
agent, so not "multi-agent". No retrieval, so not RAG. "Grounded" here means *states its
assumptions and admits uncertainty*, not *cites sources* — that meaning arrives at V0.5.
Latency (~16s/call) is unoptimised and currently unattributed.


---

### V0.2 — Tool Registry

**What was implemented:** A tool system where the agent selects and invokes capabilities
autonomously, while the system enforces argument validation, timeouts, permissions and loop
termination in code the model cannot influence.

**Evidence (files):**
- `src/amos/tools/base.py` — `Tool` ABC; concrete `execute()` performs validation and timeout so
  subclasses cannot skip either
- `src/amos/tools/registry.py` — registration, lookup, generated declarations; refuses
  `WRITE`/`DESTRUCTIVE`
- `src/amos/tools/builtin/calculator.py` — AST allowlist, never `eval()`
- `src/amos/tools/builtin/read_file.py` — resolve-then-check containment
- `src/amos/tools/builtin/http_get.py` — https + allowlist + private-IP check + no redirects
- `src/amos/agents/tool_agent.py` — bounded observe-decide-act loop

**Tests (117 total, and what they prove):**
- `test_calculator.py` — 9 code-execution payloads rejected (`__import__`, `open`,
  `().__class__.__bases__`); exponent bomb capped
- `test_read_file.py` — 5 traversal forms blocked; **symlink escape blocked**, the case a string
  filter misses
- `test_http_get.py` — non-https rejected; `evil-github.com` rejected; cloud-metadata IP
  rejected; an allowlisted host resolving internally rejected
- `test_tool_agent.py` — loop cap stops the *provider calls*, not just the result; hallucinated
  tool recovers; invalid args rejected pre-execution; injected tool output cannot widen
  permissions
- All 117 run without network access

**Demo:** verified live —
1. `"What is 17% of 2340 plus 88?"` → calls `calculator`, answers 485.8 (2 calls, ~2.1s)
2. `"Read docs/03-architecture-decisions.md and tell me why AMOS chose pgvector over Qdrant"` →
   reads its own ADR and explains the consistency argument
3. `"Read ../../../../etc/passwd"` → sandbox refuses; the model reports the refusal

**Technical explanation (unaided):** Tools declare a Pydantic input schema which serves as both
the model-facing declaration and the validator, so the two cannot drift. `Tool` is an ABC rather
than a Protocol because the base class's concrete `execute()` performs validation and timeout
enforcement — a tool has no opportunity to skip them. Tool failures are returned as data and fed
back to the model so it can correct itself, while a hard iteration cap guarantees the loop
terminates regardless of what the model does. Security is enforced by functions that never read
model output: path containment is checked *after* resolution so symlinks cannot escape, and
`http_get` uses an allowlist because a blocklist fails open.

**Likely interview questions:** `docs/interview/agents.md`.

**Honest resume wording:**
> Built a tool-execution system for an LLM agent with schema-derived tool declarations,
> pre-execution argument validation, per-tool timeouts and a permission model; hardened against
> path traversal, symlink escape, SSRF and code injection, with 117 tests running offline.

**What this does NOT demonstrate:** no persistence, no planning or decomposition, no retrieval.
One agent — **not multi-agent**. No authentication or audit trail; **not safe to expose
publicly**. The security work is defence of a single-user local tool, not a reviewed production
posture.


---

### V0.3 — Persistence and Trace

**What was implemented:** Durable recording of every run, step, LLM call and tool call in
PostgreSQL, with an endpoint that reconstructs the complete execution history of any past run
from stored rows alone, and idempotency keys that make a retried submission safe.

**Evidence (files):**
- `src/amos/database/models.py` — schema; `run_id` denormalised onto child tables for one-filter
  trace assembly
- `src/amos/database/repository.py` — persistence isolated from the domain
- `src/amos/database/engine.py` — async engine, pooling, explicit commit/rollback scope
- `src/amos/api/persistence.py` — run recorded before execution, executed outside the
  transaction, outcome recorded after
- `migrations/versions/*.py` — Alembic baseline, verified up and down
- `compose.yaml` — PostgreSQL 18 + pgvector, one service

**Tests (136 total, 18 against a real database):**
- `test_trace_is_assembled_from_stored_rows_only` — a *fresh service instance* reconstructs the
  trace, proving nothing depends on in-memory state
- `test_idempotent_resubmit_returns_the_original_run` — asserts `agent.calls == 1`; returning the
  same id while secretly re-running would be worse than useless
- `test_failed_run_keeps_its_partial_trace` — a failure that spent tokens still shows them
- `test_run_is_recorded_before_execution` — a crash leaves evidence
- Transaction-rollback isolation; 18 skip cleanly when no database is running

**Demo:**
```
POST /v1/goals  {"goal":"What is 12% of 500?"}   → run_id
GET  /v1/runs/{run_id}                            → status, result, 2 llm_calls
                                                     (provider/model/tokens/latency),
                                                     1 tool_call (name/args/output), timings
```

**Technical explanation (unaided):** The run row is written before the agent executes, so a crash
still leaves evidence the run was attempted. Execution happens outside any transaction, because an
LLM call takes seconds and holding a pooled connection across it would deadlock the pool under
concurrency — so there are three short transactions with the slow work between them. `run_id` is
denormalised onto `llm_calls` and `tool_calls` because trace assembly is the hottest read, making
it one indexed filter per table instead of a join walk. `selectinload` is used because lazy
loading raises on an async session and would be N+1 regardless. Idempotency keys mean a client
timeout followed by a retry returns the original run instead of silently doubling the work.

**Likely interview questions:** `docs/interview/persistence.md`.

**Honest resume wording:**
> Added durable execution history to an LLM agent platform using PostgreSQL and async SQLAlchemy
> with Alembic migrations: full request tracing across LLM and tool calls, idempotent submission,
> and transaction-scoped test isolation; 136 tests, of which 118 run with no database at all.

**What this does NOT demonstrate:** no planner, no concurrency, no worker or queue — `SKIP
LOCKED` is V0.8. No task-level retries. pgvector is installed but unused until V0.5. Backups are
a documented `pg_dump` command with **no restore drill**, so not a backup strategy. Still no
authentication; **not deployable publicly**. Verified on podman only — the Docker path in the
deployment doc is untested and labelled as such.


---

### V0.4 — Planner and Executor

**What was implemented:** Goal decomposition into a validated, acyclic task DAG, executed in
dependency order with independent branches running concurrently, bounded jittered retries,
transitive skipping of unreachable work, and a task state machine that raises on any transition
it does not sanction.

**Evidence (files):**
- `src/amos/orchestration/state.py` — the transition table; the only place a task's state changes
- `src/amos/orchestration/plan.py` — validation incl. iterative-DFS cycle detection returning the
  offending cycle
- `src/amos/orchestration/executor.py` — DAG walk, concurrency, retries, skip propagation,
  termination guarantee
- `src/amos/orchestration/retry.py` — exponential backoff with full jitter
- `src/amos/orchestration/orchestrator.py` — composes planner + executor + synthesis
- `src/amos/database/models.py` — `tasks` table, `depends_on UUID[]`, partial claimable index

**Tests (266 total; 130 for orchestration):**
- **All 53 illegal state transitions asserted to raise**, plus a test asserting the module's
  table and the test's expectations agree, so they cannot drift apart
- `test_skipping_propagates_transitively` — a dependent of a dependent is not left waiting
- `test_an_unrelated_branch_still_runs_when_another_fails` — failure is contained
- `test_jitter_actually_varies` — a constant would pass a bounds check while doing nothing
- `test_retry_budget_is_per_task_not_per_run`
- Cycle rejection incl. a cycle hidden behind a valid prefix, and diamond graphs accepted

**Demo (live):** *"Work out 17% of 2340 and 23% of 1500, then tell me which is larger and by how
much."* → planner emitted `t1`, `t2` (independent) and `t3` (depends on both); t1/t2 ran
concurrently; all three used the calculator; answer correct (397.8 vs 345, larger by 52.8).
8 LLM calls, 4456 tokens, 7.5s. Trace persisted with dependencies resolved to row UUIDs.

**Technical explanation (unaided):** The planner is the only LLM that decides structure, and its
output is validated — unique ids, resolvable dependencies, acyclicity — before anything is
persisted, because a cyclic plan reaching the database would be a run that can never complete.
The executor contains no LLM calls: it decides what may run, what is retried and what is skipped.
A task's state changes only through `assert_transition`, which raises on anything the table
forbids, so no model output can put the system into an unsanctioned state. Retries return a task
to `READY` rather than a special state, so a retried attempt cannot behave differently from a
first one, and backoff is jittered because tasks that fail together would otherwise retry
together indefinitely.

**Likely interview questions:** `docs/interview/orchestration.md`.

**Honest resume wording:**
> Built a task orchestration layer that decomposes goals into a validated acyclic task graph and
> executes it with dependency-aware scheduling, concurrent independent branches, an enforced state
> machine, per-task bounded retries with jittered exponential backoff, and transitive failure
> containment; 266 tests including exhaustive coverage of all 53 illegal state transitions.

**What this does NOT demonstrate:** no re-planning — a failed task is retried as written. No
worker and no queue; execution still happens inside the HTTP request, so this is **not**
distributed task processing. No resumption after a crash. **Tasks are not idempotent**, which is
safe only because every tool is read-only today. Still one agent type — **not multi-agent**. No
retrieval; pgvector remains installed and unused. Planner quality is anecdotal (n=1), not
measured — that arrives at V1.0.
