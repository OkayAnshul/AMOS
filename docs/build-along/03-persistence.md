# V0.3 — Persistence and trace

**+1,241 lines. The hinge.** After this, the planner, RAG and async execution each become possible
independently.

---

## Where you are

A tool-using agent. Everything it does vanishes when the process exits.

## The problem

Run a goal, get an answer, and the only evidence of how it got there is whatever happened to reach
a log line. You cannot answer *"what did it actually do on that request?"* an hour later, let alone
a week.

That is survivable now and fatal at V0.4, where a goal becomes a task graph. **A planner's output
is state** — you would have distributed task state living in RAM, unable to be inspected, replayed
or resumed.

## What you will have at the end

```bash
RUN=$(curl -s -X POST localhost:8000/v1/goals -d '{"goal":"..."}' | jq -r .run_id)
curl -s localhost:8000/v1/runs/$RUN | jq
```
→ the complete history of that request, reconstructed from the database by a process that never saw
it run.

---

## Decisions you are making here

### Persistence before the planner

The roadmap this project started from had reliability last. That is backwards and it is also the
expensive order — retrofitting durability across three milestones of code that assumed memory is
far more work than building the planner durable from the start.

The cost: V0.3's demo is less impressive than a planner's. It is also considerably more convincing
to an engineer.

### Postgres, and only Postgres

One store for relational data, JSONB and (from V0.5) vectors. The alternative — Postgres plus
Mongo plus a vector DB — is three backup stories and two consistency problems, for one user.

### Three short transactions, not one long one

```
1. check idempotency        (transaction)
2. create the run row       (transaction)
   ── execute the agent ──   NO transaction
3. record the outcome       (transaction)
```

**The run row is written before execution.** A crash then leaves evidence the run was attempted —
precisely the runs worth investigating. Writing afterwards loses exactly those.

**Execution happens outside any transaction.** An LLM call takes seconds; a transaction holds a
pooled connection for its lifetime. With `pool_size=5`, six concurrent goals deadlock waiting for
connections while doing nothing but network I/O.

### Persistence is optional

Without `AMOS_DATABASE_URL` the app still runs and `/v1/runs/{id}` returns 503 saying so.

Infrastructure required to run the project is infrastructure that stops people running the project
— and it would make most of your test suite depend on a container.

---

## Build order

### 1. `src/amos/database/models.py` (~200 lines)

**Why now** — the schema is the thing everything else serialises into.

**Write** — `runs`, `tasks`, `steps`, `llm_calls`, `tool_calls`.

```python
class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
    })
```

⚠️ **Write that naming convention now.** It looks like boilerplate.

<details><summary>Why it is not</summary>

Without it, SQLAlchemy lets the database invent constraint names, and Alembic then generates a
downgrade that fails with `Can't emit DROP CONSTRAINT ... it has no name`.

The migration applies perfectly and **cannot be reversed** — and you find out at the worst possible
moment. Retrofitting the convention later means rebaselining your migrations.
</details>

**Two design points worth understanding:**

**`llm_calls` and `tool_calls` carry `run_id` as well as `step_id`.** Deliberate denormalisation:
assembling a trace is the most common query in the system, and carrying `run_id` turns a four-table
join into one indexed filter. `step_id` is nullable because a planning call (V0.4) belongs to a run
before any step exists.

**`Step` is separate from `Run` even though V0.3 creates exactly one per run.** V0.4 creates many —
a retried task has several attempts, and the Task carries the final outcome while the Steps carry
*how it got there*. Collapsing them destroys the evidence retries exist to produce.

**Partial indexes**, because `READY` tasks and runs with an idempotency key are both small
fractions of their tables:
```python
Index("idx_runs_idempotency", "idempotency_key", postgresql_where=idempotency_key.isnot(None))
```

**Answer key** — `git show v0.3:src/amos/database/models.py`

---

### 2. `compose.yaml` + Alembic

One service: `pgvector/pgvector:pg18`. You need pgvector at V0.5 and it costs nothing to have it
now — and it is *one* container rather than two, which is the practical payoff of choosing pgvector
over a separate vector DB.

⚠️ **`podman-compose up -d` will report success and the container will be dead.**

<details><summary>Cause</summary>

PostgreSQL **18** images changed the volume convention: mount at `/var/lib/postgresql`, **not**
`/var/lib/postgresql/data`. Every tutorial still shows the old path, which is now refused.

**"The container started" is not "the service is running."** Put a healthcheck in the compose file
from the first version — `pg_isready`.
</details>

For Alembic: `alembic.ini` must carry **no** `sqlalchemy.url`. It is committed, and a URL with a
password does not belong in a committed file. Inject it in `migrations/env.py` from settings.

**Verify both directions before committing any migration:**
```bash
alembic upgrade head && alembic downgrade base && alembic upgrade head
```
A migration that only goes forward is a one-way door.

---

### 3. `src/amos/database/engine.py` (~60 lines)

```python
def create_engine(settings) -> AsyncEngine:
    # pool_size=5, max_overflow=5, pool_recycle=1800, pool_pre_ping=True

@asynccontextmanager
async def session_scope(factory) -> AsyncIterator[AsyncSession]:
    """Commits on success, ROLLS BACK on any exception. Explicit, not implicit."""
```

**Why one engine per process** — the engine owns the pool. Creating them per request defeats
pooling entirely, and the expensive part of a query becomes the TCP handshake.

**Why `pool_pre_ping`** — a pooled connection can be dead before it is handed out.

---

### 4. `src/amos/database/repository.py` (~180 lines)

```python
class RunRepository:
    async def find_by_idempotency_key(self, key: str) -> Run | None: ...
    async def create_run(self, *, goal, request_id, idempotency_key=None) -> Run: ...
    async def record_success(self, run: Run, result: AgentResult) -> Run: ...
    async def record_failure(self, run, error_type, message, result=None) -> Run: ...
    async def get_trace(self, run_id: uuid.UUID) -> Run | None:
        """selectinload every relationship."""
    def _add_trace_rows(self, run, step, result: AgentResult) -> None: ...
```

**Why `selectinload` and not lazy loading** — two reasons, the first fatal alone: **lazy attribute
access on an async session raises.** There is no synchronous point at which SQLAlchemy could issue
the query. Even setting that aside, it is N+1 for the system's hottest read.

**A failed run keeps its partial trace.** A failure that burned tokens must show that it did.

### 🔎 The moment of truth

Look at what `_add_trace_rows` has to do. If your V0.1 and V0.2 seams were right, it is a
**mechanical field-by-field copy** — `LLMCallRecord` → `llm_calls` row, `ToolOutcome` →
`tool_calls` row, nothing derived or restructured.

**If it is not mechanical, your seams were wrong, and that is worth knowing.** In the original it
was mechanical with exactly one gap: `ToolOutcome` had no `arguments` field, so the first working
trace showed what tools returned without what they were asked. Right in shape, ~95% right in
content.

---

### 5. `src/amos/api/persistence.py` (~150 lines) + the trace endpoint

```python
class RunService:
    async def execute(self, goal, request_id, idempotency_key=None) -> tuple[AgentResult, UUID | None]:
        # 1. idempotency check      (own transaction)
        # 2. create run row         (own transaction)  ← BEFORE executing
        # 3. run the agent          (NO transaction)
        # 4. record the outcome     (own transaction)
    async def get_trace(self, run_id) -> RunTrace | None: ...
```

**Idempotency: what it actually protects against.** A client that times out and retries. Without a
key, the retry is a whole new run — the agent executes again, tokens are spent again, and once
tools can write, side effects happen twice.

Note what it is *not*: it does not make the agent idempotent, and it does not handle two identical
requests arriving concurrently before either commits. That is a V0.8 problem.

Then `GET /v1/runs/{run_id}` → `RunTrace`, assembled from stored rows only.

---

### 6. Tests — `tests/integration/`

**Fixtures first.** Transaction-rollback isolation: each test runs inside a transaction the fixture
owns and rolls back. The code under test may commit — that lands inside the outer transaction. Far
faster than truncating.

⚠️ **Your first test will pass and every subsequent one will fail identically.**

<details><summary>Cause</summary>

`RuntimeError: got Future attached to a different loop`.

asyncpg connections are bound to the event loop that created them, and pytest-asyncio gives each
test its own loop. A **session-scoped** engine fixture hands out connections belonging to a dead
loop.

Make it function-scoped with `NullPool`. And note the diagnostic shape: **when the first test
passes and the rest fail the same way, suspect the fixtures, not the subject.**
</details>

⚠️ **Adding `AMOS_DATABASE_URL` to `.env` will break tests that have not changed.**

<details><summary>Cause</summary>

`Settings` declares `env_file=".env"`, so tests inherit your machine. Build them with
`Settings(_env_file=None, ...)`. This was latent from V0.1 and only surfaced when `.env` gained a
setting that changed behaviour.
</details>

**The test that matters most:**
```python
async def test_trace_is_assembled_from_stored_rows_only():
    # execute a run through one RunService...
    # then build a FRESH RunService and fetch the trace
    # it must be complete — nothing may depend on in-memory state
```

Also: idempotent resubmit asserts `agent.calls == 1`. Returning the same id while secretly
re-running would be worse than useless.

---

## Checkpoint

```bash
podman-compose up -d && .venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q            # ~136; DB tests skip without a container

RUN=$(curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"What is 12% of 500?"}' | jq -r .run_id)
curl -s localhost:8000/v1/runs/$RUN | jq

# and the one that proves it
podman-compose down && .venv/bin/python -m pytest -q   # must SKIP, not fail
```

That last line is the checkpoint. Your suite must degrade to skips without a database, or you have
made infrastructure mandatory.

---

## What this unlocks

Everything. V0.4's task DAG is persisted from the moment it exists. V0.5's chunks have somewhere to
live. V0.8's worker claims rows from a table that already exists.

Next: [`04-orchestration.md`](04-orchestration.md) — **the largest milestone, and the one with the
most transferable ideas.** If you stop building AMOS, stop after this one, not before it.
