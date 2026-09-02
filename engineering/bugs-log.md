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
