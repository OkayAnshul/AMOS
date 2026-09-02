# Session Log

Appended at the end of every session. Newest last.

---

# Session 1

**Date:** 2026-09-03
**Module:** Phase 0 — Architecture & Master Roadmap
**Objective:** Analyse AMOS, design the architecture, produce foundational documentation and an
incremental roadmap. No application code.

## What We Changed
Created the repository from empty: working agreement, 10 written documents, 14 stubs, six
engineering logs, git history and a private GitHub remote.

## Files Changed
All files — the repository did not exist at the start of the session.
`CLAUDE.md`, `README.md`, `.gitignore`, `.env.example`, `docs/00`–`docs/23`,
`engineering/*.md`.

## Architecture Decisions
Eight ADRs (`docs/03-architecture-decisions.md`). The three that changed the project's
direction away from the original brief:

- **ADR-001 — pgvector instead of Qdrant.** The brief named Qdrant. Qdrant's advantages appear
  at a scale AMOS will not reach, while its cost — Postgres/Qdrant dual-write inconsistency —
  appears immediately.
- **ADR-002 — persistence before the planner.** The brief sequenced reliability last. A
  planner's output *is* state; producing it before it can be persisted or inspected makes it
  undebuggable, and retrofitting durability across three milestones is the expensive order.
- **ADR-007 — 10 documents written, 14 stubbed.** The brief asked for 24 documents up front and
  separately forbade placeholder documentation. Two thirds of them would have been guesses
  presented as decisions.

## Problems Encountered
1. **Disk at 96%** — 9.4 GB free on a partition shared with `/`. Anshul cleared ~110 GB during
   the session.
2. **Recalled technology facts were wrong.** Three, each of which would have caused a real
   failure. See below.
3. **The brief contradicted itself** on documentation (§20 asks for 24 docs; §20 also forbids
   placeholders).

## How We Solved Them
1. Anshul freed the space. Noted honestly in ADR-001 that this removed one of the three original
   arguments for pgvector, and that the decision stood on the remaining one.
2. Verified everything against primary sources rather than recall. Corrections recorded in
   `docs/21-technology-baseline.md`.
3. Resolved by ADR-007 in favour of the anti-placeholder rule, since a stub can still convey
   architectural intent while an invented decision cannot be un-cited.

## Tests Performed
No code, so no tests. Verification performed instead:
- All 20 documentation URLs checked live — every one returned HTTP 200.
- Library versions, Python support and model IDs confirmed against PyPI and official docs.
- pgvector's index dimension limit confirmed against the project README.

## Current System State
Documentation only. Nothing executable. `main` has no build to break.

## Things I Learned
Concepts logged in `engineering/learning-log.md`. The session-level lesson: **verify, do not
recall.** Three of three assumed facts about a fast-moving ecosystem were wrong, and one of them
(pgvector's 2000-dimension index limit) would only have surfaced at V0.5, after an entire corpus
had been embedded at the wrong dimension.

## Things I Should Investigate
- Does `gemini-3.5-flash` structured output reliably return valid JSON, or is the repair loop
  load-bearing in practice? Measure the repair rate at V0.1 rather than assuming.
- Confirm free-tier rate limits from the account dashboard — published tables are no longer
  authoritative per model.

## References
- <https://ai.google.dev/gemini-api/docs/models>
- <https://github.com/pgvector/pgvector>
- <https://googleapis.github.io/python-genai/>

## Next Exact Step
Begin V0.1 per `engineering/current-state.md` → *Exact Next Step*. First action outside the
repository: obtain a Gemini API key.

## Recommended Commit
Already committed across seven commits on `main` and pushed. No outstanding changes.
