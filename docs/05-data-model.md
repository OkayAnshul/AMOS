# 05 — Data Model

PostgreSQL, arriving at V0.3. Schema shown as it will exist at V0.5; tables are created at the
milestone that needs them, never before (ADR-006). SQL below is illustrative of intent —
Alembic migrations are the source of truth once they exist.

## Why PostgreSQL

One store for relational data, JSON documents and vectors. JSONB covers the parts of AMOS that
are genuinely schemaless (plan payloads, tool arguments) without a second database; pgvector
covers similarity (ADR-001). The alternative — Postgres plus MongoDB plus Qdrant — means three
backup stories and two consistency problems, for one user.

## Core tables (V0.3)

```sql
CREATE TABLE runs (
    id                UUID PRIMARY KEY,
    goal_text         TEXT        NOT NULL,
    status            TEXT        NOT NULL,   -- RECEIVED|PLANNING|EXECUTING|...
    idempotency_key   TEXT,
    result            JSONB,
    error             JSONB,
    total_tokens      INTEGER     NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    CONSTRAINT runs_idempotency_unique UNIQUE (idempotency_key)
);

CREATE TABLE tasks (
    id            UUID PRIMARY KEY,
    run_id        UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_type     TEXT NOT NULL,
    parameters    JSONB NOT NULL DEFAULT '{}',
    agent_name    TEXT,
    state         TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    depends_on    UUID[] NOT NULL DEFAULT '{}',
    result        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at    TIMESTAMPTZ                      -- visibility timeout, V0.8
);

CREATE TABLE steps (                              -- one attempt at a task
    id          UUID PRIMARY KEY,
    task_id     UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt     INTEGER NOT NULL,
    status      TEXT NOT NULL,
    input       JSONB,
    output      JSONB,
    error       JSONB,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (task_id, attempt)
);

CREATE TABLE llm_calls (
    id            UUID PRIMARY KEY,
    step_id       UUID REFERENCES steps(id) ON DELETE CASCADE,
    run_id        UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms    INTEGER,
    repair_count  INTEGER NOT NULL DEFAULT 0,     -- malformed-output retries
    error         JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tool_calls (
    id           UUID PRIMARY KEY,
    step_id      UUID REFERENCES steps(id) ON DELETE CASCADE,
    run_id       UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tool_name    TEXT NOT NULL,
    arguments    JSONB NOT NULL,
    output       JSONB,
    status       TEXT NOT NULL,                   -- OK|INVALID_ARGS|TIMEOUT|ERROR
    latency_ms   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`llm_calls` and `tool_calls` carry `run_id` as well as `step_id`. That is a deliberate
denormalisation: assembling a full run trace is the single most common query in the system, and
carrying `run_id` turns a four-table join into a direct filter. `step_id` is nullable because a
planning call belongs to a Run before any Task exists.

## Knowledge tables (V0.5)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id           UUID PRIMARY KEY,
    source       TEXT NOT NULL,
    title        TEXT,
    content_hash TEXT NOT NULL,     -- re-ingest detection
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (content_hash)
);

CREATE TABLE chunks (
    id          UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    token_count INTEGER,
    embedding   vector(1536),        -- see ADR-008
    metadata    JSONB NOT NULL DEFAULT '{}',
    UNIQUE (document_id, chunk_index)
);
```

**Why 1536 and not 3072** — `gemini-embedding-001` defaults to 3072 dimensions, but pgvector's
HNSW index supports at most 2000 for the `vector` type. The default is therefore *unindexable*.
Embeddings are MRL-truncated to 1536 and **re-normalised** — truncation breaks L2 normalisation,
and cosine distance over un-normalised vectors returns wrong rankings without raising an error.
Full reasoning and the `halfvec` fallback in ADR-008.

## Indexes, and why each exists

```sql
-- trace assembly: the hot path for GET /v1/runs/{id}
CREATE INDEX idx_steps_task       ON steps(task_id);
CREATE INDEX idx_llm_calls_run    ON llm_calls(run_id);
CREATE INDEX idx_tool_calls_run   ON tool_calls(run_id);

-- executor: find claimable work (V0.4/V0.8)
CREATE INDEX idx_tasks_run_state  ON tasks(run_id, state);
CREATE INDEX idx_tasks_claimable  ON tasks(state, created_at) WHERE state = 'READY';

-- idempotency lookup on submit
CREATE INDEX idx_runs_idempotency ON runs(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- vector search
CREATE INDEX idx_chunks_embedding ON chunks
    USING hnsw (embedding vector_cosine_ops);
```

Two partial indexes rather than full ones: `READY` tasks are a small fraction of all tasks, and
most runs have no idempotency key. Indexing only the rows actually queried keeps both indexes
small enough to stay cached.

**HNSW is built at V0.5 only after the corpus is loaded** — building it on an empty table and
then inserting is markedly slower than the reverse.

## Concurrency (V0.8)

```sql
UPDATE tasks SET state = 'RUNNING', claimed_at = now(), attempt_count = attempt_count + 1
WHERE id = (
    SELECT id FROM tasks
    WHERE state = 'READY'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

`SKIP LOCKED` lets concurrent workers claim disjoint rows without blocking each other, and the
claim happens in the same transaction as the state change — so a worker that dies mid-task has
its row released by Postgres rather than by recovery code AMOS has to write (ADR-003).
`claimed_at` supports a visibility timeout: a task `RUNNING` beyond its limit is reclaimable.

Reference: <https://www.postgresql.org/docs/current/sql-select.html>

## Deliberate omissions

- **No `users` table until authentication exists.** Single user, no auth, no table. Adding one
  now would mean a foreign key everywhere pointing at one permanent row.
- **No soft deletes.** Nothing is deleted yet. `deleted_at` on every table is a cost paid
  against a hypothetical.
- **No `agents` or `tools` tables.** Agents and tools are code, registered at startup. They
  become rows only if they need to be configurable at runtime, which is not a requirement.
- **`memories` and `episodes` tables are deferred to V0.6**, when `docs/09-memory-architecture.md`
  decides what belongs in relational storage versus vectors. Guessing now would be ADR-007's
  mistake in schema form.
