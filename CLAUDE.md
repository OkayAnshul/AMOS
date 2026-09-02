# CLAUDE.md — AMOS working agreement

Read this first, every session. Then `engineering/current-state.md`.

---

## What AMOS is

An Autonomous Multi-Agent Operating System: takes a user goal, decomposes it into tasks,
assigns them to specialised agents, executes tools, retrieves knowledge, keeps memory,
validates results, and recovers from failure.

Built in **independently valuable vertical slices**. Every milestone leaves the repo runnable,
tested, demoable, documented and committed. If development stops after any milestone, what
remains is still a legitimate project.

## Two equal objectives

1. Build AMOS.
2. Anshul understands it well enough to modify, debug, explain, extend and defend it.

Objective 2 is not decoration. A milestone is not done if only objective 1 is met.

---

## Working agreement

- **Anshul does not type the code.** Claude implements; Anshul follows along. This is the
  fastest path and the weakest for retention, so retention is mechanised — see the gate below.
- **Links over prose.** Name a concept in a line or two, say what problem it solves, then link
  official documentation. No long explanatory paragraphs. Never fabricate a URL — verify it
  resolves before writing it down.
- **Challenge bad designs.** Anshul asked for this explicitly. Do not agree by default. Say
  when something is over- or under-engineered.
- **Commit and push continuously**, not batched at the end. **No `Co-Authored-By` trailer.**

## The advance gate

A milestone is not complete, and the next one does not start, until Anshul can answer that
module's questions in `docs/interview/<module>.md` unaided.

---

## Session protocol

**Start**: read `CLAUDE.md` → `engineering/current-state.md` → latest `session-log.md` entry →
relevant docs → `git status` and `git log` → identify the exact next step → verify the current
system actually runs before trusting that it does.

**End**: run tests → run lint/typecheck → verify the app starts → review the diff → update
`current-state.md`, `session-log.md`, `learning-log.md`, `decisions-log.md` if needed → record
resume evidence → state what changed, what to learn, and the exact next step → commit and push.
**Never end a session with the project knowingly broken.**

---

## Rules that constrain implementation

1. **LLMs handle uncertainty; software handles guarantees.** Task state, permissions,
   validation, retries, timeouts, persistence and security are deterministic code. The model
   never decides them.
2. **No technology without an ADR.** Every dependency answers "what problem does this solve in
   AMOS?" in `docs/03-architecture-decisions.md`, including an explicit *Reconsider if*.
   Popularity and resume value are not reasons.
3. **No placeholder artefacts.** No empty `src/` directories for unbuilt modules, no docs that
   invent decisions about systems that do not exist. A stub must say which milestone will fill
   it and what fact it is waiting on.
4. **Tests must not depend on the network.** The Gemini free tier is rate-limited; all unit and
   integration tests use `FakeProvider`. Live tests are opt-in and skipped without an API key.
5. **`main` always runs and its tests always pass.** Work in progress lives on a feature branch.
6. **Resume claims need evidence.** A claim in `docs/22-resume-evidence.md` needs a file, a test
   and a demo, or it gets cut. Never claim Kubernetes, microservices, Kafka, "distributed
   system" (it is a modular monolith) or "multi-agent" before V0.7.

---

## Current stack (verified 2026-09-03 — re-verify, do not trust memory)

| Thing | Value | Note |
|---|---|---|
| Python | 3.14.4 | only version on this machine |
| FastAPI | ≥0.119.1 | earlier versions break on 3.14 |
| Pydantic | ≥2.12 | 3.14 support landed here |
| LLM SDK | `google-genai` ≥2.21.0 | `google-generativeai` is deprecated |
| Model | `gemini-3.5-flash` | free tier |
| Embeddings | `gemini-embedding-001` @ 1536 dims | 3072 default exceeds pgvector's HNSW limit |
| Vectors | pgvector in Postgres | not Qdrant — see ADR-001 |

Model IDs and free-tier terms change. Check
<https://ai.google.dev/gemini-api/docs/models> before assuming.

## Layout

`docs/` decisions · `engineering/` running logs and recovery state · `src/` code, created
milestone by milestone · `tests/` unit, integration, contract, evaluation.
