# 02 — System Architecture

## Shape

AMOS is a **modular monolith**: one deployable process, hard internal boundaries. It stays that
way until something concrete justifies splitting it — independent scaling, isolation, or
deployment ownership. None of those exist for a single-user system, so none of them drive the
design. (ADR-004.)

## Target architecture

The destination, reached at V1.0 — not the starting point.

```
                    Client
                      │
                      ▼
                  API Layer                 FastAPI, request id, auth
                      │
                      ▼
                 Orchestrator               owns task state, retries, timeouts
                      │
                  Task DAG                  persisted, deterministic
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   Researcher      Analyst       Critic     Agent Registry
        │             │             │
        └─────────────┼─────────────┘
                      ▼
                 Tool System                schema-validated, permissioned
                      │
                      ▼
             Memory / Knowledge             PostgreSQL + pgvector
                      │
                      ▼
                Observability               OpenTelemetry
                      │
                      ▼
                 Evaluation                 golden set, CI gate
```

## Starting architecture (V0.1)

```
Client → FastAPI → Agent → LLMProvider → Gemini
```

That is the whole system at V0.1. No database, no queue, no vector store. Adding them here
would be infrastructure without a problem to solve (ADR-006).

## How V0.1 becomes V1.0 without rewrites

The evolution works because V0.1 establishes four **seams** — places where the design is
already indirect enough to absorb what comes later. Each seam costs almost nothing now and
prevents a rewrite later.

| Seam (V0.1) | Why it exists now | What it absorbs later |
|---|---|---|
| `LLMProvider` protocol | Testing needs a fake provider anyway — so the abstraction is paid for by V0.1 itself, not speculation | Groq / OpenAI drop in without touching agent code |
| `AgentResult` envelope carrying trace metadata | Structured logging needs the fields regardless | Becomes the persisted `steps` row at V0.3 — a serialisation change, not a redesign |
| Validate-and-repair loop around model output | Malformed JSON is a real V0.1 failure | Becomes tool-argument validation at V0.2 and plan validation at V0.4 — same shape, different schema |
| Request id threaded through every log line | Debugging needs it on day one | Becomes the OTel `trace_id` at V0.9 |

The load-bearing claim: **each seam is justified by a V0.1 need**. None is speculative
generality. That is the difference between designing for evolution and over-engineering.

## Layer responsibilities

**API** — HTTP, request validation, auth, request id. No business logic. Translates domain
errors into status codes.

**Orchestrator** — the deterministic core. Owns task state transitions, dependency resolution,
retries, backoff, timeouts and idempotency. Contains **no LLM calls**. This is the boundary
that makes the system predictable: the model proposes, the orchestrator disposes.

**Agents** — bounded LLM reasoning. An agent has an identity, an input and output schema, a
tool allowlist, a timeout and a retry policy. It decides *what* to do; it never decides whether
its own work succeeded — the critic and validators do (V0.7).

**Tools** — deterministic capability. Declared schemas, permissions, timeouts. Tool output is
untrusted data, never instructions (N-12).

**Memory / Knowledge** — PostgreSQL for facts, state and relations; pgvector for semantic
similarity. One store, one transaction (ADR-001).

**Observability** — request id from V0.1, structured logs from V0.1, persisted traces from
V0.3, OTel spans from V0.9. Deliberately incremental: a full observability stack installed
before there is anything to observe is cargo cult.

## The central invariant

> **LLMs handle uncertainty. Software handles guarantees.**

| Model decides | Code decides |
|---|---|
| what the goal means | whether a task may transition state |
| how to decompose it | whether a retry is permitted |
| which tool fits | whether arguments are valid |
| how to phrase an answer | whether a permission is granted |
| whether more information is needed | when to time out |

Every violation of this table is a bug. If the model can put the system into a state the code
did not sanction, the system has no guarantees — only tendencies.

## Data flow at V1.0

```
goal → validate → persist run → plan (LLM) → validate plan → persist task DAG
     → for each ready task: claim → agent (LLM) → tool calls / retrieval → validate
     → persist step → state transition → retry-or-fail
     → critic validates → assemble result → persist → respond
```

Every arrow above that says *validate*, *persist*, *transition* or *retry* is deterministic
code. Every arrow that says *LLM* is bounded by a timeout, a schema and a retry limit.
