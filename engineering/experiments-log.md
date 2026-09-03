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
