# Decisions Log

Running index of decisions. Full ADRs live in
[`../docs/03-architecture-decisions.md`](../docs/03-architecture-decisions.md); this file is the
chronological view and the home for decisions too small to warrant a full ADR.

---

## 2026-09-03 — Session 1

Eight ADRs accepted. Summary, with the *Reconsider if* that keeps each honest:

| ADR | Decision | Reconsider if |
|---|---|---|
| 001 | pgvector, not Qdrant | >5M vectors, or quantization needed |
| 002 | Persistence (V0.3) before planner (V0.4) | A deadline requires demoing planning sooner — and record that it was a presentation decision |
| 003 | Postgres `SKIP LOCKED`, not Celery/Redis | Independent worker scaling, or scheduled tasks |
| 004 | Modular monolith, not microservices | A component needs independent scaling or fault isolation |
| 005 | Gemini behind an `LLMProvider` protocol | Free-tier limits block development |
| 006 | No database at V0.1 | V0.2 needs cross-request state |
| 007 | 10 docs written, 14 stubbed | A stub's subject starts influencing implementation |
| 008 | Embeddings at 1536 dims, re-normalised | Measured recall@1536 materially worse than 3072 |

### Smaller decisions

**No `src/` directories until they hold code.**
*Context:* the brief's suggested layout lists ten `src/` subdirectories.
*Decision:* create each at the milestone that fills it.
*Why:* an empty `agents/` directory claims progress that does not exist — the structural form of
the placeholder-documentation problem (ADR-007).
*Reconsider if:* never; a directory costs nothing to create when it is actually needed.

**Tests never touch the network.**
*Context:* free-tier Gemini is ~15 RPM and non-deterministic.
*Decision:* `FakeProvider` in all unit and integration tests; one live smoke test, skipped
without an API key.
*Why:* network tests would be slow, flaky, rate-limited and would fail in CI. Locked as N-14.
*Reconsider if:* never for unit tests. A separate, opt-in live suite may grow at V1.0.

**No `Co-Authored-By` trailer on commits.**
*Context:* Anshul's explicit instruction, reversing an earlier choice to keep it.
*Decision:* omit at commit time rather than adding and stripping afterwards.

**GitHub repository private until v0.1.**
*Context:* the repo becomes public evidence for placements.
*Decision:* private now; public when V0.1 runs with passing tests.
*Why:* a visitor's first impression should be a working project, not an empty `src/`.
