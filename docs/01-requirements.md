# 01 — Requirements

Requirements are tagged with the milestone that makes them true. Untagged means V0.1.
A requirement that is not yet met is not a lie here — it is a scheduled commitment.

## Functional

| ID | Requirement | Milestone |
|---|---|---|
| F-1 | Accept a natural-language goal over an HTTP API and return a structured, schema-valid response | V0.1 |
| F-2 | Support multiple LLM providers behind one interface; swapping providers changes no calling code | V0.1 |
| F-3 | Recover from malformed model output by re-prompting, bounded, then fail with a typed error | V0.1 |
| F-4 | Register tools with declared input/output schemas, timeouts and permissions | V0.2 |
| F-5 | Let the agent select and invoke tools autonomously, with arguments validated before execution | V0.2 |
| F-6 | Bound the agent loop — no unlimited tool-calling | V0.2 |
| F-7 | Persist every run, step, tool call and LLM call durably | V0.3 |
| F-8 | Expose the full execution trace of any past run by id | V0.3 |
| F-9 | Deduplicate resubmitted goals via a client-supplied idempotency key | V0.3 |
| F-10 | Decompose a goal into a validated, acyclic task graph | V0.4 |
| F-11 | Execute tasks respecting dependencies; retry failures with backoff; propagate unrecoverable failure to dependents | V0.4 |
| F-12 | Ingest documents: parse, chunk, embed, store with metadata | V0.5 |
| F-13 | Retrieve relevant chunks and answer with citations; refuse rather than fabricate when retrieval is empty | V0.5 |
| F-14 | Retain facts and prior run outcomes across sessions and use them in later runs | V0.6 |
| F-15 | Route tasks to specialised agents; a critic validates output and can force a retry | V0.7 |
| F-16 | Accept long-running goals asynchronously and report progress | V0.8 |
| F-17 | Emit distributed traces and metrics for every run | V0.9 |
| F-18 | Score system quality against a golden goal set and fail CI on regression | V1.0 |

## Non-functional

**Correctness**
- N-1 Every LLM output crossing a system boundary is schema-validated before use. Unvalidated
  model text never becomes control flow.
- N-2 State transitions are enforced in code. An invalid transition raises; it does not warn.
- N-3 Retried operations are idempotent, or explicitly documented as not-yet-safe. (V0.3)

**Reliability**
- N-4 Every external call has a timeout. No unbounded wait, ever.
- N-5 Retries are bounded and backed off. Infinite retry is a bug, not a policy.
- N-6 A worker crash mid-task loses no work; the task becomes claimable again. (V0.8)

**Observability**
- N-7 Every request carries an id, threaded through logs and later trace spans.
- N-8 Latency, token usage, retry count and errors are recorded per LLM call.
- N-9 "What happened on run X?" is answerable from stored data alone. (V0.3)

**Security**
- N-10 Secrets come from the environment. No credential is ever committed.
- N-11 Tools declare permissions; network tools use an explicit allowlist. (V0.2)
- N-12 Retrieved and tool-returned content is untrusted input — it is data, never instructions.
- N-13 Irreversible actions require validation, permission and human approval before execution.

**Performance** — deliberately loose. A single user on a laptop against a rate-limited free
tier makes throughput targets meaningless theatre. The real constraint is 15 requests/minute
upstream, so tests never touch the network (N-14). Latency is *recorded* from V0.1 so that a
target can be set later from data rather than guessed now.

**Maintainability**
- N-15 New dependencies require an ADR with an explicit *Reconsider if*.
- N-16 `main` always runs and its tests always pass.
- N-17 `engineering/current-state.md` is sufficient to resume cold after months away.

## Explicit non-goals

Multi-tenancy. Horizontal scale. High availability. Real-time streaming UI. Fine-tuning or
model training. Mobile clients. These are not deferred-and-planned; they are **out of scope**,
and pretending otherwise would distort the architecture.
