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

**V0.1 shipped.** One claim is now evidenced. Everything below it remains unbuilt and unclaimable.

## Evidence table

| Claim | Milestone | Files | Tests | Demo | Status |
|---|---|---|---|---|---|
| Typed LLM service with provider abstraction and validated structured output | V0.1 | `src/amos/llm/base.py`, `src/amos/agents/agent.py`, `src/amos/api/app.py` | 41 tests, `tests/unit/test_agent_repair.py` | `POST /v1/goals` | ✅ **shipped** |
| Tool-using agent with schema-validated arguments, timeouts and permission allowlists | V0.2 | — | — | — | ⬜ not built |
| Durable execution history with full request tracing and idempotent submission | V0.3 | — | — | — | ⬜ not built |
| Goal decomposition into a persisted task DAG with enforced state machine and bounded retries | V0.4 | — | — | — | ⬜ not built |
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
