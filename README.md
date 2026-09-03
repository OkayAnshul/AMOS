# AMOS — Autonomous Multi-Agent Operating System

An AI platform that takes a complex goal, decomposes it into tasks, assigns them to specialised
agents, executes tools, retrieves knowledge, keeps memory, validates results, and recovers from
failure.

> **Status: V0.2 shipped — runnable.** An agent that selects and executes tools autonomously,
> with validation, timeouts and permissions enforced in code. No database yet (by design,
> ADR-006). See [`engineering/current-state.md`](engineering/current-state.md) for exactly where
> things stand.

---

## Why this repo looks the way it does

AMOS is built as **independently valuable vertical slices**, not as modules that only work once
the last one lands. Every milestone leaves the repository runnable, tested, demoable and
documented. Stop at any version and there is still a real project here.

That principle has visible consequences:

- `src/` contains only what V0.1 built. Directories for unbuilt modules would be a lie about
  progress, so each appears at the milestone that fills it.
- Most of `docs/` is one-line stubs naming the milestone that will write them. A
  chunking-strategy document written before anything has been embedded would be inventing
  decisions, not recording them.
- No Docker and no database. They arrive at V0.3, when durable runs are the milestone — each
  justified by an ADR in
  [`docs/03-architecture-decisions.md`](docs/03-architecture-decisions.md).

## Roadmap at a glance

| V | Milestone | What you can honestly show if development stops here |
|---|---|---|
| 0.1 ✅ | Grounded agent API | A typed, tested LLM service with provider abstraction and validated outputs |
| **0.2 ✅** | **Tool registry** | **An agent that autonomously selects and executes validated tools** |
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

Requires Python 3.14+ and a free Gemini API key from <https://aistudio.google.com/apikey>.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env        # then add your key to AMOS_GEMINI_API_KEY
.venv/bin/python -m amos    # serves on http://127.0.0.1:8000
```

Ask it something:

```bash
curl -s -X POST localhost:8000/v1/goals \
  -H 'content-type: application/json' \
  -d '{"goal":"What is 17% of 2340 plus 88?"}' | jq
```

It picks a tool, runs it, and shows you what it did:

```json
{
  "response": {"answer": "485.8", "confidence": "high", "assumptions": [...]},
  "tool_outcomes": [
    {"name": "calculator", "status": "ok", "output": {"result": 485.8}, "latency_ms": 0}
  ],
  "total_tokens": 1149,
  "latency_ms": 2120
}
```

Ask it about its own design — it will read the file:

```bash
-d '{"goal":"Read docs/03-architecture-decisions.md and tell me why AMOS chose pgvector."}'
```

Ask it to escape its sandbox — it cannot:

```bash
-d '{"goal":"Read ../../../../etc/passwd"}'
# -> read_file: invalid_args, "resolves outside the permitted directory"
```

<details>
<summary>V0.1 example (no tools)</summary>

You get the answer plus how it was produced — token cost, latency, and whether the model had to
be corrected:

```json
{
  "request_id": "33e066d4fa3d4265",
  "response": {
    "answer": "PostgreSQL SKIP LOCKED",
    "reasoning": "...",
    "assumptions": ["AMOS already uses or has access to a PostgreSQL database."],
    "confidence": "high",
    "caveats": ["If AMOS requires extreme throughput ... Redis would be a better fit."]
  },
  "llm_calls": [{"provider": "gemini", "model": "gemini-3.5-flash",
                 "prompt_tokens": 103, "output_tokens": 259, "repair_attempt": 0}],
  "repair_count": 0,
  "total_tokens": 362,
  "latency_ms": 16743
}
```

</details>

Interactive API docs at <http://127.0.0.1:8000/docs>.

### ⚠️ Free-tier quota

The Gemini free tier is **20 requests per day, per model** — not a per-minute rate limit. A
tool-using goal costs 2 calls. `AMOS_LLM_MODEL` defaults to `gemini-3.5-flash-lite` because
quota is per model, which keeps `gemini-3.5-flash`'s allowance free for demos.

## Testing

```bash
.venv/bin/python -m pytest        # 117 tests, no network
.venv/bin/mypy src                # strict
.venv/bin/ruff check src tests
```

Tests never call the real API — the free tier is ~15 RPM and non-deterministic, so a network
dependency would make the suite slow and flaky. `FakeProvider` scripts the model's responses,
including the malformed ones that exercise the repair loop.

One live smoke test is opt-in:

```bash
AMOS_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live -v
```

## Repository layout

```
src/amos/
  config.py         settings from env, fails fast on a missing key
  errors.py         typed error hierarchy
  observability.py  structured JSON logs + request id
  llm/              LLMProvider protocol, GeminiProvider, FakeProvider
  tools/            Tool ABC, registry, calculator / read_file / http_get
  agents/           validate-and-repair loop + bounded tool loop
  api/              FastAPI app, error -> status mapping
tests/              unit, integration, live (opt-in)
docs/               architecture and decisions (14 written, 10 stubs)
engineering/        current-state, session log, learning log, decisions, bugs, experiments
CLAUDE.md           working agreement and session protocol
```

## A note on authorship

Built by [@OkayAnshul](https://github.com/OkayAnshul) with an AI pair-programmer, as a
deliberate exercise in backend and AI systems engineering. Every claim in
[`docs/22-resume-evidence.md`](docs/22-resume-evidence.md) is backed by a file, a test and a
demo — or it is not claimed.
