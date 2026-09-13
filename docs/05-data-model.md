# 05 — Data Model

PostgreSQL. The schema as it exists at **V1.1**, built up across five migrations, each created at
the milestone that needed it and never before (ADR-006).

> **The migrations in `migrations/versions/` are the source of truth**, and
> `src/amos/database/models.py` is checked against the live database by
> `tests/integration/test_schema_drift.py`. The SQL below is written to match them, not to
> precede them.
>
> It did precede them once, and drifted badly: this document described `tasks.claimed_at`, a
> partial `idx_tasks_claimable` index, and task-level `SKIP LOCKED` claiming for months after
> V0.8 **deleted all three** in favour of run-level claiming. A reader learning the schema here
> would have learned the design AMOS rejected. That is what the warning above is for.

Migration chain: `e25051359e64` (V0.4 baseline) → `5a881f4bdb98` (V0.5 pgvector) →
`a0621f74b57c` (V0.6 memory) → `5892709841cc` (V0.8 run claiming) → `453890cfd6a9`
(V1.1 trace continuity).

## Why PostgreSQL

One store for relational data, JSON documents and vectors. JSONB covers the parts of AMOS that
are genuinely schemaless (plan payloads, tool arguments) without a second database; pgvector
covers similarity (ADR-001). The alternative — Postgres plus MongoDB plus Qdrant — means three
backup stories and two consistency problems, for one user.

## Core tables

```sql
CREATE TABLE runs (
    id                UUID PRIMARY KEY,
    goal_text         TEXT        NOT NULL,
    status            TEXT        NOT NULL,   -- QUEUED|RUNNING|COMPLETED|FAILED|DEAD_LETTER|...
    idempotency_key   TEXT,                   -- nullable, so the index is partial
    request_id        TEXT,                   -- V0.1's request id, threaded through
    result            JSONB,
    error             JSONB,
    total_tokens      INTEGER     NOT NULL DEFAULT 0,
    latency_ms        INTEGER     NOT NULL DEFAULT 0,
    lesson            TEXT,                   -- episodic memory, V0.6
    claimed_at        TIMESTAMPTZ,            -- run-level claiming, V0.8
    claimed_by        TEXT,                   -- "hostname:pid" of the worker
    attempt_count     INTEGER     NOT NULL DEFAULT 0,
    trace_parent      TEXT,                   -- W3C traceparent of the enqueuing request, V1.1
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    goal_embedding    vector(1536),           -- episodic recall by goal similarity, V0.6
    CONSTRAINT runs_idempotency_unique UNIQUE (idempotency_key)
);

CREATE TABLE tasks (                          -- one unit of work in a plan, V0.4
    id            UUID PRIMARY KEY,
    run_id        UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    plan_ref      TEXT NOT NULL,              -- the planner's own id, e.g. "t1"
    description   TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'PENDING',
    depends_on    UUID[] NOT NULL DEFAULT '{}',
    position      INTEGER NOT NULL DEFAULT 0, -- topological order
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    result        JSONB,
    error         JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tasks_run_planref_unique UNIQUE (run_id, plan_ref)
);

CREATE TABLE steps (                          -- one attempt at doing work
    id          UUID PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES runs(id)  ON DELETE CASCADE,
    task_id     UUID     REFERENCES tasks(id) ON DELETE CASCADE,  -- nullable, see below
    attempt     INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    input       JSONB,
    output      JSONB,
    error       JSONB,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CONSTRAINT steps_task_attempt_unique UNIQUE (task_id, attempt)
);

CREATE TABLE llm_calls (
    id             UUID PRIMARY KEY,
    run_id         UUID NOT NULL REFERENCES runs(id)  ON DELETE CASCADE,
    step_id        UUID     REFERENCES steps(id) ON DELETE CASCADE,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    latency_ms     INTEGER NOT NULL DEFAULT 0,
    repair_attempt INTEGER NOT NULL DEFAULT 0,   -- which malformed-output retry this was
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tool_calls (
    id         UUID PRIMARY KEY,
    run_id     UUID NOT NULL REFERENCES runs(id)  ON DELETE CASCADE,
    step_id    UUID     REFERENCES steps(id) ON DELETE CASCADE,
    call_id    TEXT NOT NULL,                     -- the model's own id for the call
    tool_name  TEXT NOT NULL,
    arguments  JSONB NOT NULL DEFAULT '{}',
    output     JSONB,
    status     TEXT NOT NULL,                     -- OK|INVALID_ARGS|TIMEOUT|NOT_FOUND|ERROR
    error      TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Three modelling decisions worth defending:**

`llm_calls` and `tool_calls` carry `run_id` **as well as** `step_id`. That is a deliberate
denormalisation: assembling a full run trace is the most common query in the system, and
carrying `run_id` turns a multi-table join into a direct filter. `step_id` is nullable because a
planning call belongs to a Run before any Task exists.

`steps.task_id` is **nullable and `run_id` is not** — the reverse of what the shape suggests. A
V0.3 run has a step with no task, because tasks did not exist yet; from V0.4 every step belongs
to one, and a task retried three times has three steps. That is what makes "was this retried?"
answerable from stored data rather than from logs.

`tasks.depends_on` is a Postgres `UUID[]`, not a join table. The textbook normalisation is a
`task_dependencies` table, but every read of this graph loads the whole run's tasks at once
anyway — the join buys nothing and costs a table.

## Memory table (V0.6)

```sql
CREATE TABLE memories (
    id            UUID PRIMARY KEY,
    subject       TEXT   NOT NULL,             -- normalised key, for exact lookup
    content       TEXT   NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    source_run_id UUID REFERENCES runs(id)     ON DELETE SET NULL,
    superseded_by UUID REFERENCES memories(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding     vector(1536)
);
```

**Why a relational table and not just vectors**, since "remember things" in an AI system usually
means embed-everything:

1. **Exact recall is a key lookup.** "What is the user's name?" must return *the* name, not the
   most similar-looking fact. Similarity search will occasionally return someone else's.
2. **Contradictions need ordering.** `superseded_by` is a chain, not a delete — the previous
   value stays auditable and "current" is the deterministic query `superseded_by IS NULL`.
3. **Provenance is a join.** "Where did this come from?" is a foreign key to a run.

`embedding` still exists, for questions that have no key. It is the secondary path, never a
substitute for the exact one.

**There is no `episodes` table.** An episode *is* a run, so episodic memory is `runs.lesson` and
`runs.goal_embedding` rather than a second table duplicating goal, status, tokens and timings to
add two columns. Full reasoning in `docs/09-memory-architecture.md`.

## Knowledge tables (V0.5)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id           UUID PRIMARY KEY,
    source       TEXT NOT NULL,
    title        TEXT,
    content_hash TEXT NOT NULL,     -- re-ingest detection
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT documents_content_hash_unique UNIQUE (content_hash)
);

CREATE TABLE chunks (
    id          UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    heading     TEXT,                -- the section it came from, for citations
    token_count INTEGER,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding   vector(1536),        -- see ADR-008
    CONSTRAINT chunks_document_index_unique UNIQUE (document_id, chunk_index)
);
```

**Why 1536 and not 3072** — `gemini-embedding-001` defaults to 3072 dimensions, but pgvector's
HNSW index supports at most 2000 for the `vector` type. The default is therefore *unindexable*.
Embeddings are MRL-truncated to 1536 and **re-normalised** — truncation breaks L2 normalisation,
and cosine distance over un-normalised vectors returns wrong rankings without raising an error.
Full reasoning and the `halfvec` fallback in ADR-008.

Every `vector` column is added by raw SQL in its migration, because SQLAlchemy core has no
`vector` type. They are therefore **absent from `models.py`** and explicitly allowlisted in
`test_schema_drift.py`, so the drift check stays meaningful rather than quietly ignoring them.

## Indexes, and why each exists

```sql
-- trace assembly: the hot path for GET /v1/runs/{id}
CREATE INDEX idx_steps_run        ON steps(run_id);
CREATE INDEX idx_steps_task       ON steps(task_id);
CREATE INDEX idx_llm_calls_run    ON llm_calls(run_id);
CREATE INDEX idx_tool_calls_run   ON tool_calls(run_id);

-- executor: the tasks of one run, by state
CREATE INDEX idx_tasks_run_state  ON tasks(run_id, state);

-- worker: the claim path (V0.8)
CREATE INDEX idx_runs_claimable   ON runs(status, created_at) WHERE status = 'QUEUED';
CREATE INDEX idx_runs_created     ON runs(created_at);

-- idempotency lookup on submit
CREATE INDEX idx_runs_idempotency ON runs(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- memory: current facts only
CREATE INDEX idx_memories_current     ON memories(subject) WHERE superseded_by IS NULL;
CREATE INDEX idx_memories_source_run  ON memories(source_run_id);

-- retrieval
CREATE INDEX idx_documents_source ON documents(source);
CREATE INDEX idx_chunks_document  ON chunks(document_id);
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
```

**Three partial indexes** rather than full ones. `QUEUED` runs are a small and shrinking fraction
of every run ever executed; most runs have no idempotency key; and superseded memories
accumulate without the hot path ever reading them. Indexing only the rows actually queried keeps
each index small enough to stay cached.

**HNSW is built after the corpus is loaded** — building it on an empty table and then inserting
is markedly slower than the reverse. The query must use the matching `<=>` operator, or Postgres
silently falls back to a sequential scan: a correctness-shaped performance bug that raises no error.

## Concurrency (V0.8) — claiming is per *run*

```sql
UPDATE runs
   SET status = 'RUNNING', claimed_at = now(), claimed_by = :worker,
       attempt_count = attempt_count + 1
 WHERE id = (
       SELECT id FROM runs
        WHERE status = 'QUEUED' AND attempt_count < :max_attempts
        ORDER BY created_at
          FOR UPDATE SKIP LOCKED
        LIMIT 1
 )
RETURNING id, goal_text, attempt_count;
```

`SKIP LOCKED` lets concurrent workers claim disjoint rows without blocking each other, and the
claim happens **in the same transaction as the state change** — so a worker that dies mid-run has
its row released by Postgres rather than by recovery code AMOS would have to write and test
(ADR-003). `claimed_at` supports the visibility timeout: a run `RUNNING` beyond its limit is
reclaimable.

**Run, not task.** V0.4 added `tasks.claimed_at` and a partial claimable index on the reasoning
that adding a column later to a populated table is a migration. The reasoning was sound and the
granularity was wrong: a run is what a client submits and polls, and a run's internal task
concurrency is already handled by `asyncio.gather` inside the executor. Distributing individual
tasks would mean distributing the executor. V0.8 **deleted both** rather than carry schema
documenting an abandoned plan.

Reference: <https://www.postgresql.org/docs/current/sql-select.html>

## Deliberate omissions

- **No `users` table until authentication exists.** Single user, no auth, no table. Adding one
  now would mean a foreign key everywhere pointing at one permanent row. *Scheduled for V1.4,
  which is where `user_id` arrives on `runs`, `memories` and `documents` together.*
- **No soft deletes.** Nothing is deleted yet. `deleted_at` on every table is a cost paid
  against a hypothetical.
- **No `agents` or `tools` tables.** Agents and tools are code, registered at startup. They
  become rows only if they need to be configurable at runtime, which is not a requirement.
- **No `task_dependencies` join table** — see `depends_on` above.
- **No dead-letter *table*.** V1.1 added the dead-letter *queue*, but as a status value on
  `runs` rather than a second table — the rows are identical to any other run's, and a
  `DEAD_LETTER` run is simply one the queue stopped retrying. `GET /v1/runs/dead-letter` is the
  read. A separate table would duplicate every column to add nothing.
