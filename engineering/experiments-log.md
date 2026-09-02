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
