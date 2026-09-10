# 0 — Setup, and the decisions you are inheriting

**No application code in this step.** You are setting up an environment and — more importantly —
understanding six decisions that were made before a line was written, because they constrain
everything you type afterwards.

---

## Before anything: measure your machine

The first thing done on this project was not choosing a framework. It was checking facts:

```bash
python3 --version          # AMOS needs 3.14+
df -h ~                    # container images are not small
docker --version || podman --version
```

Half the decisions below depend on facts about the machine. The original build started with 9.4 GB
free on a partition shared with `/`, which is one large download from an unbootable laptop — that
was a genuine architectural input until it was cleared.

**And verify library facts rather than recalling them.** Three assumptions were wrong at the start
of this project:

| Assumed | Actually |
|---|---|
| `google-generativeai` is the SDK | **Deprecated.** `google-genai` is current |
| Models are `gemini-1.5-*` | The **3.x** line |
| pgvector indexes any dimension | **2000 max** for HNSW — and the embedding model defaults to 3072 |

The third would have surfaced at V0.5, *after* embedding an entire corpus at an unindexable size.
Checking one README saved re-embedding everything five milestones later.

**The rule, and it recurs throughout this guide: never write a version number, model ID or API
signature from memory.**

---

## The six decisions you are inheriting

You do not have to agree with these. You do have to understand them, because you will be asked
about every one. Full reasoning: [`../03-architecture-decisions.md`](../03-architecture-decisions.md).

### 1. pgvector, not a dedicated vector database (ADR-001)

Not a performance decision — at this scale both are far past sufficient. It is about
**consistency**: with a separate vector DB, a chunk's row lives in Postgres and its embedding lives
elsewhere, so every write is a distributed write with no shared transaction. Postgres commits, the
vector store fails, and your index now disagrees with your source of truth.

*Reconsider if:* vectors exceed ~5M, or you need quantization.

### 2. Persistence before the planner (ADR-002)

A planner's output **is** state. The moment a goal becomes a task graph you have distributed task
state — you just have it in RAM, where it cannot be inspected, replayed or resumed.

So V0.3 is persistence and V0.4 is the planner, not the other way round. It also happens to be the
cheaper order: retrofitting durability across three milestones of code that assumed memory is
expensive.

### 3. A modular monolith, not microservices (ADR-004)

Microservices solve organisational problems — independent deployment, team ownership, fault
isolation. You have one developer and one machine. You would pay the full price for none of the
benefit.

**Consequence you must respect: "distributed system" is never a claim this project makes.**

### 4. Postgres `SKIP LOCKED`, not Celery (ADR-003)

You will already have Postgres and already be persisting runs. `SKIP LOCKED` turns that table into
a correct work queue, and the claim happens in the *same transaction* as the state change — so a
dead worker is cleaned up by the database rather than by recovery code you have to write and test.

### 5. Gemini behind a Protocol (ADR-005)

One provider implemented, the seam for others built immediately — and **not** speculatively. Your
tests need a fake provider from day one, because the free tier is rate-limited and
non-deterministic. The abstraction is paid for by V0.1's own tests; multi-provider support is a
side effect.

### 6. No database at V0.1 (ADR-006)

The one that is uncomfortable to follow, because a database feels like seriousness.

Nothing in V0.1 needs to survive a restart. Adding Postgres means Docker, a schema, migrations and
connection lifecycle — real complexity against no requirement — and it makes V0.1 impossible to run
without infrastructure.

**You will want to add it anyway. Don't.**

---

## What to create

```
~/amos-by-hand/
├── .gitignore
├── .env.example
├── pyproject.toml
└── src/amos/__init__.py        (empty for now)
```

### `.gitignore` — first, before anything else

`.env` must be ignored **before** it exists. A key committed once is a key in the history forever,
and you will be putting a real one in shortly.

```
.env
*.key
__pycache__/
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
```

### `pyproject.toml`

Pin the floors and understand why each one is a floor:

| Dependency | Floor | Why that number |
|---|---|---|
| `fastapi` | ≥0.119.1 | Earlier versions break on Python 3.14 |
| `pydantic` | ≥2.12 | Python 3.14 support landed there |
| `google-genai` | ≥2.21.0 | The current SDK. Not `google-generativeai` |
| `pytest`, `ruff`, `mypy` | latest | dev extras |

Three settings that matter more than they look:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]              # ⚠️ see the trap below
asyncio_mode = "auto"

[tool.mypy]
strict = true                     # not optional; it catches real bugs in this project

[tool.hatch.build.targets.editable]
dev-mode-dirs = ["src"]           # src/ layout needs this explicitly
```

**Answer key:** `git show v0.1:pyproject.toml`

### Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "import amos; print('ok')"
```

---

## Checkpoint

```bash
.venv/bin/python -c "import amos; print(amos.__file__)"
# must print a path inside YOUR src/ directory
```

If that fails, stop. Everything else builds on it.

---

## ⚠️ Traps

**`pip show` says the package is installed and `import amos` raises `ModuleNotFoundError`.**

Worse: your tests will pass anyway, so nothing tells you.

<details><summary>Ten minutes first — then open</summary>

A `src/` layout needs an explicit editable target. Without
`[tool.hatch.build.targets.editable] dev-mode-dirs = ["src"]`, pip writes dist-info metadata and no
path hook.

Tests pass because `pythonpath = ["src"]` in `pyproject.toml` bypasses the broken mechanism
entirely.

**This is the lesson the whole project keeps re-learning: a green test suite does not prove the
application starts.** It is why every milestone in this guide ends with running something, not just
testing it. See `engineering/bugs-log.md`, 2026-09-03.
</details>

---

## What this unlocks

Nothing yet. That is the point — you have an environment and six decisions, and no code to defend.

Next: [`01-foundation.md`](01-foundation.md), where you write a working service and deliberately
do not give it a database.
