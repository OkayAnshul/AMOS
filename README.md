# AMOS — Autonomous Multi-Agent Operating System

An AI platform that takes a complex goal, decomposes it into tasks, assigns them to specialised
agents, executes tools, retrieves knowledge, keeps memory, validates results, and recovers from
failure.

> **Status: Phase 0 — architecture and roadmap.** No application code yet. The first
> runnable milestone is V0.1. See [`engineering/current-state.md`](engineering/current-state.md)
> for exactly where things stand.

---

## Why this repo looks the way it does

AMOS is built as **independently valuable vertical slices**, not as modules that only work once
the last one lands. Every milestone leaves the repository runnable, tested, demoable and
documented. Stop at any version and there is still a real project here.

That principle has visible consequences:

- There is no `src/` yet, because there is no code yet. Empty directories for unbuilt modules
  would be a lie about progress.
- Two thirds of `docs/` are one-line stubs naming the milestone that will write them. A
  chunking-strategy document written before anything has been embedded would be inventing
  decisions, not recording them.
- No Docker, no database, no dependencies. They arrive at the milestone that needs them and
  not before — each justified by an ADR in
  [`docs/03-architecture-decisions.md`](docs/03-architecture-decisions.md).

## Roadmap at a glance

| V | Milestone | What you can honestly show if development stops here |
|---|---|---|
| 0.1 | Grounded agent API | A typed, tested LLM service with provider abstraction and validated outputs |
| 0.2 | Tool registry | An agent that autonomously selects and executes validated tools |
| 0.3 | Persistence + trace | "What exactly happened on this request?" — answerable for any run |
| 0.4 | Planner / Executor | Goal decomposition into a durable task DAG with deterministic state |
| 0.5 | RAG | A retrieval pipeline with citations and a measured recall@k |
| 0.6 | Memory tiers | Recalls user facts and prior run outcomes across sessions |
| 0.7 | Multi-agent | Specialised agents collaborate; a critic gates output |
| 0.8 | Async execution | Long-running goals execute asynchronously with crash-safe job claiming |
| 0.9 | Observability | Full distributed trace of any run |
| 1.0 | Evaluation | Quality is measured, not asserted |

Full detail, including failure modes and stopping points, in
[`docs/19-roadmap.md`](docs/19-roadmap.md).

## Architecture

Target (the destination, not the starting point):

```
Client → API → Orchestrator → Task DAG → Agent Registry → {Researcher, Analyst, Critic}
                                              ↓
                                        Tool System
                                              ↓
                                  Memory / Knowledge (Postgres + pgvector)
                                              ↓
                                  Observability → Evaluation
```

Today it is a modular monolith and will stay one until something concrete justifies splitting
it. See [`docs/02-system-architecture.md`](docs/02-system-architecture.md).

## Stack

Python 3.14 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 + Alembic (V0.3) · PostgreSQL + pgvector
(V0.3/V0.5) · `google-genai` / Gemini · pytest · Docker Compose (V0.3).

Not used, and not claimed: Kubernetes, Kafka, Celery, microservices.

## Running it

Nothing to run yet — V0.1 is the first executable milestone. Setup instructions land with it,
and `engineering/current-state.md` carries the current *How To Run* and *How To Test* at all
times.

## Repository layout

```
docs/          architecture and decisions (7 written, 17 stubs awaiting their milestone)
engineering/   current-state, session log, learning log, decisions, bugs, experiments
CLAUDE.md      working agreement and session protocol
```

## A note on authorship

Built by [@OkayAnshul](https://github.com/OkayAnshul) with an AI pair-programmer, as a
deliberate exercise in backend and AI systems engineering. Every claim in
[`docs/22-resume-evidence.md`](docs/22-resume-evidence.md) is backed by a file, a test and a
demo — or it is not claimed.
