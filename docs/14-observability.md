# 14 — Observability

**Written at V0.9.**

## What was already there

Observability did not start here. Since **V0.1** every log line has carried a request id, and
since **V0.3** every run, LLM call and tool call has been persisted and queryable via
`GET /v1/runs/{id}`.

So this milestone is not "add observability" — it is "emit it in a standard format, with
structure". The correlation problem was solved eight milestones ago, which is why V0.9 is small.

| Layer | Since | Answers |
|---|---|---|
| Structured JSON logs + request id | V0.1 | what happened, in order |
| Persisted run trace | V0.3 | what happened on *that* run, weeks later |
| OpenTelemetry spans | V0.9 | where the time went, in standard tooling |
| Metrics | V0.9 | how the system behaves in aggregate |

## Spans

```
run.execute                         amos.run_id, amos.goal.length
├── llm.complete                    gen_ai.system, gen_ai.request.model, token counts
├── tool.execute                    amos.tool.name, amos.tool.status, latency
└── llm.complete
```

Attribute names follow the OpenTelemetry **GenAI semantic conventions** (`gen_ai.system`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens`) where they exist, so a standard backend
understands them without custom dashboards.

**The request id from V0.1 is attached to every span**, which is what lets a trace be correlated
with the structured logs that predate tracing entirely.

Verified against a local collector:

```
Name : run.execute     amos.goal.length: Int(19)      amos.request_id: 0013d45719d8413d
Name : llm.complete    gen_ai.request.model: gemini-3.5-flash-lite
Name : tool.execute    amos.tool.name: calculator
Name : llm.complete    gen_ai.request.model: gemini-3.5-flash-lite
```

## Two rules, enforced by tests

### 1. User content does not leave the process by default

A goal is user content. It can contain names, a document someone pasted, or a credential. Spans
are shipped to a collector, stored, and usually searchable — **putting user input in them by
default is a data-handling decision disguised as a debugging convenience.**

So `amos.goal.length` is always recorded and `amos.goal.text` only when `AMOS_TRACE_CONTENT=true`.

The default is **off**, deliberately: an opt-*out* would ship user content from every deployment
that forgot to configure it. Defaults are what most systems actually run with.

There is also a forbidden-key filter (`api_key`, `password`, `token`, …) applied to every span
attribute — belt and braces, because a helper that silently drops known-sensitive keys is more
reliable than remembering not to pass them.

### 2. Unbounded values never become metric labels

A run id as a *span attribute* is fine — a span is one event. The same value as a **metric label**
creates one time series per run, which is the standard way to take a monitoring backend down.

```python
ALLOWED_LABELS = {"provider", "model", "tool", "status", "agent", "outcome"}
```

All closed sets. `safe_labels()` drops everything else, and it is an **allowlist rather than a
blocklist** for the same reason `http_get` uses one: a blocklist must anticipate every unbounded
field and fails open when it misses one.

`test_unbounded_values_cannot_become_metric_labels` asserts `run_id`, `goal`, `user`,
`request_id` and `error_message` are all rejected.

## Metrics

| Metric | Type | Labels |
|---|---|---|
| `amos.llm.calls` | counter | provider, model |
| `amos.llm.tokens` | counter | provider, model |
| `amos.tool.calls` | counter | tool, status |
| `amos.runs` | counter | outcome |
| `amos.run.duration` | histogram | outcome |
| `amos.task.retries` | counter | status |

Token counts are *values*, not dimensions — summed, never grouped by.

## No-op by default

Tracing and metrics are inert unless `AMOS_OTLP_ENDPOINT` is set. The OpenTelemetry API is built
for this: with no provider configured every span call is a cheap no-op, so call sites need no
branching.

Consequences, both deliberate: **the test suite needs no collector**, and **the app runs with no
observability stack at all** — the same principle as the optional database at V0.3.

## Running it

```bash
podman-compose --profile observability up -d otel-collector
AMOS_OTLP_ENDPOINT=http://localhost:4318/v1/traces .venv/bin/python -m amos
podman logs -f amos-otel
```

The collector is **local and prints to its log**. No Jaeger, no Tempo, no Prometheus, no SaaS.
The point of this milestone is that AMOS emits correct, well-shaped telemetry; where it is stored
is a deployment decision, and standing up a full stack for a single-user project is exactly the
"massive observability stack" the roadmap warns against.

## Not done

- **No log/trace correlation via trace id in log output.** Logs carry the request id and spans
  carry it too, so correlation works — but the logs do not yet emit the W3C `trace_id`
- **No sampling.** Every span is exported. Fine at this volume, wrong at any real one
- **No context propagation across the worker boundary.** A queued run starts a new trace rather
  than continuing the submitting request's — the run id links them, but they are two traces
- **No RED/USE dashboards** — metrics are emitted, nothing consumes them
- **No alerting**, no SLOs
- **FastAPI is not auto-instrumented** — HTTP-level spans would come free from
  `opentelemetry-instrumentation-fastapi`, deliberately not added since the interesting spans are
  the ones AMOS creates itself
