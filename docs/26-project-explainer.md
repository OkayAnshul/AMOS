# 26 — Project explainer

What AMOS is, how it works inside, and how to present it. Written 2026-11-19 at V1.4, for explaining
the project to someone who has not read the rest of `docs/`. Numbers here are copied from
`engineering/current-state.md`; if they disagree, that file wins.

---

## In one paragraph

AMOS takes a goal in plain English, splits it into a graph of tasks, gives each task to a
specialised AI agent with its own tools, checks the answer with a separate critic agent, and
returns a cited answer. Every step is stored in PostgreSQL, so any run can be replayed and
inspected afterwards. It is a FastAPI service of about 10,000 lines of Python, with 618 tests.

## The problem it solves

A single chatbot call gives one pass of plausible text: no decomposition, no retrieval of what
your project actually is, no check that the claims hold, no record of how it got there, and no
recovery when part of it fails. The gap is not the model; it is the **system around the model**.
(`docs/00-vision.md`)

## The one idea to explain first

> **LLMs handle uncertainty. Software handles guarantees.**

| The model decides | Plain code decides |
|---|---|
| what the goal means, how to split it | whether a task may change state |
| which tool fits | whether the tool's arguments are valid |
| how to phrase the answer | whether a permission is granted, when to time out, whether to retry |

If the model can push the system into a state the code did not allow, that is a bug.
(`docs/02-system-architecture.md`)

---

## Architecture

A **modular monolith**: one deployable service with hard internal boundaries — not microservices
(ADR-004).

```
Client → API (FastAPI: request id, API-key auth)
       → Orchestrator        plain code only: task state, retries, timeouts — no LLM calls
       → Task DAG            persisted in PostgreSQL as it runs
       → Agents              Researcher | Analyst | Critic
       → Tools               schema-validated, allowlisted per agent
       → Memory / knowledge  PostgreSQL + pgvector
       → Observability       OpenTelemetry traces
       → Evaluation          fixed goal sets, regression gate
```

**One run, step by step:** goal → route to an agent → planner (LLM) proposes a task DAG → code
validates and stores it → each ready task runs: agent → tool calls / retrieval → validated →
stored → critic reviews → cited answer → stored → returned.

## Where AI is involved

| Part | Role | Code |
|---|---|---|
| LLM provider | Gemini behind one `LLMProvider` interface; tests use `FakeProvider`, so no test touches the network | `src/amos/llm/` |
| Router | picks which agent gets a task | `agents/router.py` |
| Planner | proposes the task DAG; code validates it before anything runs | `orchestration/planner.py` |
| Researcher | knowledge search, allowlisted fetch, file read, memory | `agents/registry.py` |
| Analyst | calculator only; must use it for every arithmetic step | `agents/registry.py` |
| Critic | **no tools** — only judges whether claims are supported | `agents/critic.py` |
| Delegation | an agent may ask another for help: depth 1, budget 3 per run, never itself — enforced by code | `agents/delegation.py` |
| RAG | documents → chunks → embeddings (`gemini-embedding-001`, 1536 dims) → pgvector → cited answers | `src/amos/rag/` |
| Memory | semantic (facts) and episodic (past runs); 2 of 5 designed kinds built | `src/amos/memory/` |
| Judge | an LLM scores groundedness during evaluation | `evaluation/judge.py` |

## Components with no AI

- **Async workers** — a PostgreSQL-backed queue. A killed worker's run is reclaimed by another and
  **resumes** without redoing finished tasks (V1.1). Runs that keep failing land in a
  dead-letter list.
- **Tracing** — one trace per run, across processes (OpenTelemetry).
- **Auth** — hashed API keys, and every query scoped to its owner (V1.4).
- **Evaluation** — golden goals, retrieval recall, routing accuracy; `make eval` fails on a
  regression.

## How it was built

Fourteen milestones, V0.1 to V1.4, each leaving the project runnable, tested and committed
(`docs/19-roadmap.md`, `docs/25-build-journal.md`). V0.1 was only API → agent → Gemini; every
later layer was added because the previous one had a concrete need for it.

Built with an AI pair-programmer under a written working agreement (`docs/working-agreement.md`):
the assistant implements, Anshul designs, reviews and must pass the interview questions in
`docs/interview/` for each module. Say this plainly if asked — the interview gate is what makes
the claim "I can explain and defend it" true.

## What is measured

| What | Result | Caveat |
|---|---|---|
| End-to-end goals | 9/9, refusals 2/2 | on the old corpus; re-measure after the rebuild |
| Retrieval | recall@5 100%, recall@1 62.5%, MRR 0.771 | 16 questions, 850 chunks, 2026-11-19; most rank-1 misses are unlabelled correct answers |
| Routing | 13/15 | both misses arguably better answers since delegation |

**All sets are small and written by the builder** — a regression gate, not proof of quality.
Saying so first is stronger than being asked.

## What it does not do (say it before being asked)

- Not distributed: several processes, one machine, one database.
- At-least-once execution: a crash at the wrong moment can redo one task.
- No re-planning: a resumed run follows its stored plan, even a bad one.
- Authentication, not authorization: no roles or scopes.
- Not safe to expose publicly: no TLS, rate limiting or key rotation.
- **No web search yet**: the researcher reads only its corpus and seven documentation sites, so
  "plan a trip" is refused honestly rather than invented. The demo track (`docs/19-roadmap.md`,
  V1.5–V2.0) adds web search, complex-goal handling and a web page.

---

## Demo queries

Today (after the corpus is rebuilt):

| # | Goal | What it shows |
|---|---|---|
| 1 | Calculate 44% of 250 and then subtract 10. | routing to the analyst, a 2-step plan, the calculator |
| 2 | According to the AMOS documentation, why was pgvector chosen over Qdrant? | RAG with a citation to `03-architecture-decisions.md` |
| 3 | What does the documentation say about how retries avoid a thundering herd? | retrieval of an engineering concept (jitter) |
| 4 | Look up the daily request quota for gemini-3.5-flash in the docs, then calculate how many 5-call goals fit in a week. | delegation: researcher looks up, analyst computes |
| 5 | How is AMOS deployed on Kubernetes? | honest refusal: the docs mention Kubernetes only to reject it |
| 6 | "Remember that my project review is on Friday." then, in a new run, "When is my project review?" | memory across runs |
| 7 | Submit an async goal, kill the worker mid-run, start another | crash recovery without redoing finished tasks |

After V1.7–V1.8 (web search and complex goals):

| # | Goal | What it shows |
|---|---|---|
| 8 | Plan a 3-day Goa trip under ₹15,000: find typical hostel, food and transport costs, then split the budget per day. | all three agents, web citations, arithmetic, the critic rejecting unsourced costs |
| 9 | Compare PostgreSQL LISTEN/NOTIFY and Redis Streams for a job queue and recommend one for a single-machine project. | research → comparison table → reasoned recommendation |

## Talk track — six points

1. **The problem**: a chat reply cannot plan, verify or recover.
2. **The invariant**: the model proposes, code disposes — the table above.
3. **Vertical slices**: fourteen milestones, each one working.
4. **Evidence**: numbers you can re-run with one command, with their caveats.
5. **Limits stated up front**: the list above.
6. **A failure story**: an `alembic downgrade` run as a check destroyed the corpus, and there was
   no backup — which is why V1.5 builds a restore drill. (`engineering/bugs-log.md`)

## Read next

`docs/25-build-journal.md` (the narrative) → `docs/02-system-architecture.md` →
`docs/interview/*.md` in milestone order.
