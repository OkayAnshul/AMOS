# Interview — Observability (V0.9)

**Advance gate: V1.0 does not begin until these can be answered unaided.**

---

### Why was V0.9 the smallest milestone in the project?

Because the hard part was done in V0.1. Every log line has carried a request id since the first
milestone, and every run has been persisted and queryable since V0.3. **Correlation was already
solved.** V0.9 adds a standard wire format and span structure on top of it.

That is the payoff for a seam built eight milestones earlier for a completely different reason:
the request id existed because debugging needed it, not because tracing was planned.

### Why is goal text not recorded in spans by default?

Because a goal is user content. It can contain a name, a document someone pasted, or a credential
they included by accident. Spans are shipped to a collector, stored, and usually searchable.

**Putting user input in telemetry by default is a data-handling decision disguised as a debugging
convenience.** So `amos.goal.length` is always recorded, and the text only under
`AMOS_TRACE_CONTENT=true`.

The direction of the default matters: an opt-*out* would ship user content from every deployment
that forgot to configure it, and defaults are what most systems actually run with.

### What is the cardinality problem, and how does the code prevent it?

A metric label creates one time series per distinct value. `run_id` as a label means one series
per run, forever — which is the standard way to take a monitoring backend down.

The same value as a **span attribute** is fine, because a span is one event rather than a
dimension.

So `ALLOWED_LABELS` is a closed set — `provider`, `model`, `tool`, `status`, `agent`, `outcome` —
and `safe_labels()` drops everything else. It is an **allowlist, not a blocklist**, for the same
reason `http_get` uses one: a blocklist has to anticipate every unbounded field and fails open
when it misses one.

### Why is tracing a no-op by default rather than always on?

Two consequences, both deliberate: the test suite needs no collector, and the app runs with no
observability stack at all. Same principle as the optional database at V0.3 — infrastructure that
is required to run the project is infrastructure that stops people running the project.

The OpenTelemetry API is designed for this: with no provider configured, every span call is a
cheap no-op, so call sites need no branching.

### Why a local collector instead of a hosted backend?

Because the milestone's claim is that **AMOS emits correct, well-shaped telemetry**. Where it is
stored is a deployment decision. Standing up Jaeger, Tempo and Prometheus for a single-user
project is exactly the "massive observability stack" the roadmap warns against — and shipping
traces to a vendor is a data decision nothing here justifies.

The collector prints to its log. That is enough to verify the spans are right.

### A trace and a persisted run trace are both "traces". What is the difference?

They answer different questions from different storage.

- `GET /v1/runs/{id}` (V0.3) answers *"what happened on this run"* — durable, queryable weeks
  later, and available with no observability stack running.
- OpenTelemetry spans (V0.9) answer *"where did the time go, and how does this compare to other
  runs"* — in standard tooling, with waterfall views.

The V0.3 trace is the source of truth. The spans are for analysis.

### What is broken about your tracing today?

Worth answering directly rather than being asked:

**Context does not propagate across the worker boundary.** A queued run starts a *new* trace
rather than continuing the submitting request's. The run id links them, but a backend shows two
traces where there should be one. Fixing it means serialising the W3C trace context into the run
row and restoring it in the worker.

Also: no sampling (every span exported — fine at this volume, wrong at any real one), and the logs
carry the request id but not the W3C `trace_id`, so log→trace correlation needs a lookup.

### How do you know the safety rules actually hold?

Tests, and then a live check. `test_goal_text_is_not_recorded_by_default` and
`test_unbounded_values_cannot_become_metric_labels` assert them, and the demo against a real
collector showed:

```
run.execute    amos.goal.length: Int(19)    amos.request_id: 0013d45719d8413d
```

Length, not text — for a goal that was 19 characters long. The rule holding in the exported data,
not only in a unit test.

---

## What V0.9 does NOT demonstrate

- **No trace-context propagation into workers** — a queued run is a separate trace
- **No sampling** — every span exported
- **No dashboards, no alerting, no SLOs** — metrics are emitted, nothing consumes them
- **FastAPI is not auto-instrumented**, so there are no HTTP-level spans
- Logs do not carry the W3C `trace_id`
- One local collector printing to a log. **Nothing here is evidence of production monitoring**
