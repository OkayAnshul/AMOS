# 18 — Deployment

**Written at V0.3**, when AMOS first needed infrastructure.

## What runs

Two processes: the FastAPI app, and PostgreSQL in a container. That is the whole topology, and
it stays that way until something concrete justifies more (ADR-004).

```
python -m amos  ──▶  PostgreSQL 18 + pgvector   (container, port 5432)
```

**No Kubernetes, no orchestration, no cloud.** AMOS runs on one machine for one user. Adding
Kubernetes would be adding an answer to a question nobody asked (ADR-004).

## Why one database service

PostgreSQL carries relational data, JSONB documents and (from V0.5) vectors. Qdrant would mean a
second stateful service, a second backup story, and a dual-write consistency problem between the
two — for a corpus that will not exceed a few tens of thousands of vectors. ADR-001.

## Running it

```bash
podman-compose up -d          # or: docker compose up -d
.venv/bin/alembic upgrade head
.venv/bin/python -m amos
```

Stop with `podman-compose down`. Add `-v` to also delete the data volume.

### Podman vs Docker

`compose.yaml` works with both, unchanged. Podman is the default here because it is rootless and
daemonless — no `docker` group, so no logout/login before the tooling works.

Verified on **podman 5.8.2 + podman-compose**. The Docker path is the same file and the same
image; where a command differs, both are shown.

### The PostgreSQL 18 volume path

```yaml
volumes:
  - amos-pgdata:/var/lib/postgresql      # NOT /var/lib/postgresql/data
```

PostgreSQL 18 images changed this convention. The image now places data in a version-named
subdirectory so `pg_upgrade --link` works across a single mount boundary. Mounting the old
`/var/lib/postgresql/data` path makes the container **exit(1)** with an explanatory message that
is easy to miss. This cost a debugging cycle; see `engineering/bugs-log.md`.

## Migrations

Schema lives in version control, never applied by hand.

```bash
.venv/bin/alembic revision --autogenerate -m "what changed"
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade -1
```

`alembic.ini` deliberately carries **no** `sqlalchemy.url` — it is committed, and a database URL
with a password does not belong in a committed file. `migrations/env.py` injects it from
`AMOS_DATABASE_URL` instead.

Every migration is verified both ways before it is committed. A migration that cannot be
reversed is a one-way door.

## Configuration

Everything from the environment (12-factor). `.env` is gitignored; `.env.example` shows the
shape with no real values. A missing key or URL fails at **startup**, not on the first request.

| Variable | Purpose |
|---|---|
| `AMOS_GEMINI_API_KEY` | required |
| `AMOS_DATABASE_URL` | `postgresql+asyncpg://amos:amos@localhost:5432/amos` |
| `AMOS_LLM_MODEL` | defaults to `gemini-3.5-flash-lite` — free-tier quota is per model and daily |
| `AMOS_AGENT_MAX_ITERATIONS` | hard cap on tool-calling rounds |

## Running without a database

Deliberately supported. With `AMOS_DATABASE_URL` unset, the app starts, serves goals, and
returns `503` from `/v1/runs/{id}` with an explanatory message. V0.1 and V0.2 behaviour stays
reachable, and the test suite needs no container — 118 of 136 tests pass with nothing running.

Optional infrastructure is what keeps "every milestone is runnable" true after V0.3.

## Backups

```bash
podman exec amos-postgres pg_dump -U amos amos > backup.sql
```

Not automated, and no restore drill has been performed — so this is a documented command, not a
backup strategy, and should not be described as one.

## Not done, and not claimed

No CI, no container image for AMOS itself, no reverse proxy, no TLS, no secrets manager, no
monitoring, no health-checked readiness probe distinct from liveness.

**AMOS is not deployed anywhere and is not safe to expose publicly** — there is no
authentication (`docs/13-security.md`). "Production" is not a word that applies to it.
