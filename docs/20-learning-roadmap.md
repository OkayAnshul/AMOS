# 20 — Learning Roadmap

The second objective of AMOS: Anshul can modify, debug, explain, extend and defend it.

**Anshul does not type the code.** That is the fastest path to a working system and the weakest
for retention, so retention is mechanised rather than hoped for.

## The mechanism

1. **Pre-read** — before a milestone starts, its concepts are named with official links. One
   line on the problem each solves, then the link. No essays.
2. **Build** — implementation with the reasoning stated at each decision point.
3. **Explain-back** — Anshul explains each decision; probing questions follow until the
   explanation holds. Getting this wrong is the mechanism working, not failing.
4. **Interview doc** — `docs/interview/<module>.md` records why / alternatives / tradeoffs /
   failure modes / scaling.
5. **Advance gate** — **the next milestone does not start** until that module's questions can
   be answered unaided.

Concepts and links per milestone live in [`../engineering/learning-log.md`](../engineering/learning-log.md),
which is a link index, not an essay.

## Concept progression

| Milestone | Concepts introduced |
|---|---|
| V0.1 | async Python and ASGI · Pydantic validation · structured LLM output · `Protocol` and dependency injection · test fakes · 12-factor config |
| V0.2 | function calling · JSON Schema · bounded agent loops · allowlists · prompt injection |
| V0.3 | async ORM sessions · migrations · transactions · **idempotency** · connection pooling |
| V0.4 | DAGs and topological order · state machines · exponential backoff and jitter · partial failure |
| V0.5 | chunking · embeddings and cosine distance · MRL truncation and re-normalisation · HNSW · recall@k · grounding |
| V0.6 | memory taxonomy · relational vs vector placement · contradiction resolution |
| V0.7 | agent specialisation · structured inter-agent contracts · critic/reflection · routing accuracy |
| V0.8 | at-least-once delivery · `SKIP LOCKED` · visibility timeouts · idempotent workers · why exactly-once is unavailable |
| V0.9 | traces vs metrics vs logs · spans · sampling · cardinality |
| V1.0 | golden datasets · LLM-as-judge and its limits · regression gating |

## The questions that matter most

If only ten things are retained, these — they are what a skeptical interviewer actually probes:

1. Why is a retry dangerous without idempotency?
2. Why can the LLM not move a task between states?
3. What exactly does `SKIP LOCKED` lock, and what happens when a worker dies mid-task?
4. Why is exactly-once delivery unavailable, and what is done instead?
5. Why 1536 dimensions, and what breaks without re-normalisation after truncation?
6. Why pgvector rather than Qdrant — and at what point would that flip?
7. What stops a tool-calling loop from running forever?
8. Why is `Step` a separate entity from `Task`?
9. How is retrieval quality *measured* rather than asserted?
10. Why is this a modular monolith and not microservices?

## Honest self-assessment scale

Applied per concept in the learning log:

- **Recognise** — the word is familiar
- **Explain** — can describe it unprompted to someone else
- **Apply** — can implement it in a new context without reference
- **Defend** — can argue the tradeoff and name when the choice would be wrong

**Defend** is the target for anything appearing in `22-resume-evidence.md`. Claiming a
capability that sits at *Recognise* is how interviews go badly.
