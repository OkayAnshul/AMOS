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

**Phase 0 complete. No implementation. Therefore: no claims.**

AMOS may not currently be listed as a built project. What exists is an architecture and a
roadmap — real work, and describable as such ("designed the architecture for…"), but not as a
system that runs.

## Evidence table

| Claim | Milestone | Files | Tests | Demo | Status |
|---|---|---|---|---|---|
| Typed LLM service with provider abstraction and validated structured output | V0.1 | — | — | — | ⬜ not built |
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
