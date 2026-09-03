# Experiments Log

Things tried to answer a question, whether or not they worked. Failed experiments are kept —
they are the record of what was ruled out, and re-running a dead end is pure waste.

**Format:**
```
## YYYY-MM-DD — Question being answered
Hypothesis:
Method:
Result:              numbers, not impressions
Conclusion:
Decision affected:   which ADR or milestone this fed into
```

---

## No experiments yet

Phase 0 involved verification (checking documented facts), not experimentation (measuring
unknown behaviour). Verification results are in `docs/21-technology-baseline.md`.

## Experiments already queued

| Question | Milestone | Why it must be measured rather than assumed |
|---|---|---|
| How often does `gemini-3.5-flash` structured output actually fail validation? | V0.1 | Determines whether the repair loop is load-bearing or dead code. Assuming either way is a guess. |
| Chunk size vs recall@k on the AMOS corpus | V0.5 | `docs/10-rag-architecture.md` must contain measured numbers, not defaults copied from a blog post |
| Recall at 1536 vs 3072 dimensions | V0.5 | Tests ADR-008's central assumption; the fallback is `halfvec(3072)` |
| Routing accuracy across specialised agents | V0.7 | "Multi-agent" is only claimable with a routing number behind it |
| Poll interval vs latency and DB load | V0.8 | Sets the `SKIP LOCKED` poll interval from data rather than by feel |

---

## 2026-09-03 — How often does `gemini-3.5-flash` structured output fail validation?
**Milestone:** V0.1
**Hypothesis:** structured output makes conforming JSON likely but not certain, so the repair
loop will occasionally fire.
**Method:** `repair_count` is recorded on every `AgentResult`. Observed across the live smoke
test and two manual demo calls.
**Result:** `repair_count = 0` on 3/3 real calls. Token cost 183–362 per call; latency 8–17s.
**Conclusion:** *Inconclusive — n=3 is not a measurement.* No evidence yet that the repair loop
ever fires against this model, and equally none that it does not.
**Decision affected:** none yet. The loop stays: it is defensive against truncation
(`MAX_TOKENS`), safety stops, and Pydantic constraints the provider does not enforce — none of
which a 3-call sample would surface. Revisit at V0.4, when a plan means several sequential
calls and longer outputs make truncation materially more likely.
**Next:** log `repair_count` across normal V0.2 usage and check whether it is ever non-zero
before deciding the loop is dead code.

**Note on latency:** ~16s for a single call is higher than expected and currently unattributed
(model? network? this campus connection?). Recorded as technical debt in `current-state.md`;
worth attributing before V0.4 makes calls sequential.

---

## 2026-09-03 — What is the Gemini free tier actually limited to?
**Milestone:** V0.2
**Hypothesis:** ~15 requests/minute, per every published summary and per AMOS's own error text.
**Method:** ran the live tool tests and demos until a 429 arrived, then read the full quota
violation in the error body rather than the status code alone.
**Result:**
```
quotaId:    GenerateRequestsPerDayPerProjectPerModel-FreeTier
model:      gemini-3.5-flash
limit:      20
retryDelay: 23s
```
**Conclusion:** **20 requests per DAY per model.** Not per minute. The published guidance was
wrong, and AMOS's own error message repeated it back.
**Decision affected:**
- `AMOS_LLM_MODEL` now defaults to `gemini-3.5-flash-lite` — quota is per model, so development
  no longer consumes the allowance reserved for demos.
- The redundant `_finalise` call was removed. At 20/day a wasted call per goal is a third of
  the budget, which promoted it from optimisation to correctness.
- The rate-limit error now carries the provider's own quota text and retry delay.
**Also found:** `gemini-2.5-flash` returns **404 NOT_FOUND** — no longer served, despite being
recorded as the fallback model in Phase 0.

---

## 2026-09-03 — Can tools and `response_schema` be sent in the same request?
**Milestone:** V0.2
**Hypothesis:** no — assumed while writing the tool loop, and (wrongly) annotated as verified.
**Method:** one request with both `tools` and `response_schema` set.
**Result:** **accepted.** The model returned a function call, with the schema still configured.
**Conclusion:** the assumption was false and had cost one API call per goal.
**Decision affected:** schema requested on every loop turn; measured 3 calls → 2 calls,
8.4s → 2.8s end to end.

---

## 2026-09-03 — Does the repair loop ever fire? (running)
**Milestone:** V0.1–V0.2
**Result so far:** `repair_count = 0` across every real call to date (n≈8).
**Conclusion:** still inconclusive, and now less likely to fire, since structured output is
requested on every turn. Keep recording. Revisit at V0.4, where longer outputs make truncation
(`MAX_TOKENS`) materially more likely — the failure mode the loop most plausibly protects
against.
**Latency note:** the ~16s/call recorded at V0.1 was network variance, not a property of the
system. `gemini-3.5-flash-lite` measures ~1s/call; a full 2-call tool goal completes in ~2.1s.

---

## 2026-09-03 — Did the V0.1/V0.2 seams actually hold under persistence?
**Milestone:** V0.3
**Question:** ADR-002 and `docs/02-system-architecture.md` claimed `AgentResult`,
`LLMCallRecord` and `ToolOutcome` were shaped so that V0.3 would be a *serialisation change*
rather than a redesign. Was that true, or optimistic?
**Method:** implemented persistence and inspected what
`RunRepository._add_trace_rows` had to do.
**Result:** it is a mechanical field-by-field copy. No field had to be derived, inferred or
restructured. `AgentResult.llm_calls` → `llm_calls` rows and `AgentResult.tool_outcomes` →
`tool_calls` rows, one to one.

**One genuine gap:** `ToolOutcome` carried the tool's *result* but not the *arguments* it was
called with, so the first working trace showed what came back without what was asked. Added
`ToolOutcome.arguments`.

**Conclusion:** the seam was right in shape and ~95% right in content. Recording the imperfection
because "it worked perfectly" would be the less useful claim — the missing field is exactly the
kind of thing this experiment existed to find.
**Decision affected:** none reversed. Evidence that designing V0.4's task rows against
`docs/05-data-model.md` up front is worth the same effort.
