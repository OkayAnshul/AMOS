# V0.9 — Observability

**+518 lines. The smallest milestone in the project — and the reason it is small is the lesson.**

---

## Where you are

A system that plans, routes, uses tools, retrieves, remembers, executes asynchronously and survives
worker crashes.

## Why this is small

**Observability did not start here.** Since V0.1 every log line has carried a request id. Since V0.3
every run, LLM call and tool call has been persisted and queryable.

**Correlation was solved eight milestones ago** — by a request id added because debugging needed
one, not because tracing was planned. This milestone adds a standard wire format on top of work
already done.

That is what a seam is worth. Nine milestones later, the thing it enables costs an afternoon instead
of a refactor.

| Layer | Since | Answers |
|---|---|---|
| Structured logs + request id | V0.1 | what happened, in order |
| Persisted run trace | V0.3 | what happened on *that* run, weeks later |
| OpenTelemetry spans | V0.9 | where the time went, in standard tooling |
| Metrics | V0.9 | how the system behaves in aggregate |

---

## Two rules — and they are tests, not documentation

### 1. User content does not leave the process by default

A goal is user content. It can contain a name, a document someone pasted, or a credential they
included by accident. **Spans are shipped to a collector, stored, and usually searchable.**

Putting user input in telemetry by default is a data-handling decision disguised as a debugging
convenience.

So: `amos.goal.length` always, `amos.goal.text` only under `AMOS_TRACE_CONTENT=true`.

**The direction of the default is the whole decision.** An opt-*out* ships user content from every
deployment that forgot to configure it, and defaults are what systems actually run with.

### 2. Unbounded values never become metric labels

A metric label creates **one time series per distinct value**. `run_id` as a label means one series
per run, forever — the standard way to take a monitoring backend down.

The same value as a **span attribute** is fine, because a span is one event rather than a dimension.

```python
ALLOWED_LABELS = frozenset({"provider", "model", "tool", "status", "agent", "outcome"})
```

All closed sets. **An allowlist, not a blocklist** — same reasoning as `http_get`: a blocklist must
anticipate every unbounded field and fails open when it misses one.

---

## Build order

### 1. `src/amos/telemetry/tracing.py` (~162 lines)

```python
def configure_tracing(settings) -> bool:
    """Returns whether tracing is active. No endpoint -> False, and every span
       call becomes a cheap no-op through the OTel API's default provider."""

def safe_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Drops forbidden keys (api_key, password, token, ...) and None values."""

@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Records exceptions and SETS AN ERROR STATUS. Attaches the request id."""

def record_llm_call(span, *, provider, model, prompt_tokens, output_tokens, latency_ms) -> None:
def describe_goal(goal: str, include_content: bool) -> dict[str, Any]:
```

**Why `span()` must set an error status** — a span that ends without one **looks successful in every
backend**. A failure that was not explicitly marked is a failure you will not find.

**Why `None` values are dropped** — OTel rejects them, and a span that fails to record because one
optional field was absent is worse than a span missing that field.

**Follow the GenAI semantic conventions** where they exist — `gen_ai.system`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens`. A standard backend then understands your spans
without custom dashboards.

**Test first**
```python
def test_goal_text_is_not_recorded_by_default(): ...
def test_content_tracing_is_off_by_default_in_settings(): ...
@pytest.mark.parametrize("key", ["api_key", "password", "token", ...])
def test_sensitive_attributes_are_dropped(key): ...
def test_an_exception_marks_the_span_as_an_error(): ...
def test_spans_are_safe_to_create_when_tracing_is_disabled(): ...
```

⚠️ **Your span-capture fixture will work for the first test and record nothing thereafter.**

<details><summary>Cause</summary>

`trace.set_tracer_provider()` can only be called **once per process**. Later calls are ignored
**with a warning, not an error** — so a per-test provider silently records nothing after the first
test, presenting as "no spans captured" rather than "your fixture is wrong".

One module-level provider, clear the exporter per test.
</details>

⚠️ **And your telemetry test will break an unrelated test in a file you did not touch.**

<details><summary>Cause</summary>

`set_request_id()` writes a module-level `ContextVar`, which leaks into every test that runs
afterwards in the same process.

Add an **autouse** fixture resetting it. Autouse, because remembering to clean up global state per
test is exactly the discipline that fails silently.
</details>

---

### 2. `src/amos/telemetry/metrics.py` (~120 lines)

```python
def configure_metrics(settings) -> bool: ...
def instruments() -> _Instruments:
    """Lazily created, so importing this module never starts a meter provider."""
def safe_labels(**labels: Any) -> dict[str, Any]:
    """Keeps only ALLOWED_LABELS. Everything else is dropped."""
```

| Metric | Type | Labels |
|---|---|---|
| `amos.llm.calls` | counter | provider, model |
| `amos.llm.tokens` | counter | provider, model |
| `amos.tool.calls` | counter | tool, status |
| `amos.runs` | counter | outcome |
| `amos.run.duration` | histogram | outcome |

**Token counts are values, not dimensions** — summed, never grouped by.

**Test first**
```python
@pytest.mark.parametrize("label", ["run_id", "goal", "user", "request_id", "error_message"])
def test_unbounded_values_cannot_become_metric_labels(label):
    assert safe_labels(**{label: "anything"}) == {}

def test_label_allowlist_is_not_a_blocklist(): ...
```

---

### 3. Instrument the hot paths — and notice the retrofit

Add spans to `llm/gemini.py` (one per call), `tools/base.py` (one per tool execution), and
`api/persistence.py` (one per run).

**This is the milestone that reaches backwards.** `tools/base.py` gains `telemetry` imports five
milestones after it was written. If you were building in dependency order rather than historical
order, you would have written telemetry first with nothing to observe.

That is exactly why this guide follows the milestones.

Keep the instrumentation thin — wrap, do not restructure. In `tools/base.py`, `execute()` becomes a
span wrapper around a new `_execute_inner()` holding the original body.

---

### 4. A local collector

```yaml
# compose.yaml, behind a profile
otel-collector:
  image: docker.io/otel/opentelemetry-collector-contrib:latest
  ports: ["4318:4318"]
  profiles: ["observability"]
```

with a config that exports to `debug` — printing to its own log.

**No Jaeger, no Tempo, no Prometheus, no SaaS.** The claim of this milestone is that your system
*emits correct, well-shaped telemetry*. Where it is stored is a deployment decision, and standing up
a full stack for a single-user project is exactly the "massive observability stack" the roadmap
warns against.

---

## Checkpoint

```bash
podman-compose --profile observability up -d otel-collector
AMOS_OTLP_ENDPOINT=http://localhost:4318/v1/traces .venv/bin/python -m amos &

curl -s -X POST localhost:8000/v1/goals -d '{"goal":"What is 44% of 250?"}'
podman logs amos-otel | grep -E "Name|amos\.|gen_ai\."
```

Expected:
```
Name : run.execute     amos.goal.length: Int(19)     amos.request_id: 0013d45719d8413d
Name : llm.complete    gen_ai.request.model: gemini-3.5-flash-lite
Name : tool.execute    amos.tool.name: calculator
Name : llm.complete    gen_ai.request.model: gemini-3.5-flash-lite
```

**Two things to check, and they are the milestone:**

1. **`amos.goal.length`, and no `amos.goal.text`.** Length, not content — the rule holding in
   exported data rather than only in a unit test.
2. **The same `amos.request_id` on every span.** That is the V0.1 seam paying off nine milestones
   later.

And confirm it is genuinely optional:
```bash
unset AMOS_OTLP_ENDPOINT && .venv/bin/python -m pytest -q   # must pass, no collector needed
```

---

## What to say about it, including what is broken

Worth answering directly rather than being asked:

**Trace context does not propagate into workers.** A queued run starts a *new* trace rather than
continuing the submitting request's. The run id links them, but a backend shows two traces where
there should be one. Fixing it means serialising the W3C trace context into the run row and
restoring it in the worker.

Also: no sampling (every span exported — fine at this volume, wrong at any real one), no dashboards,
no alerting, and the logs carry the request id but not the W3C `trace_id`.

Next: [`10-evaluation.md`](10-evaluation.md) — the last milestone, where you find out whether any of
this is actually any good.
