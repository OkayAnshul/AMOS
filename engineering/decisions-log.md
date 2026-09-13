# Decisions Log

Running index of decisions. Full ADRs live in
[`../docs/03-architecture-decisions.md`](../docs/03-architecture-decisions.md); this file is the
chronological view and the home for decisions too small to warrant a full ADR.

---

## 2026-09-03 — Session 1

Eight ADRs accepted. Summary, with the *Reconsider if* that keeps each honest:

| ADR | Decision | Reconsider if |
|---|---|---|
| 001 | pgvector, not Qdrant | >5M vectors, or quantization needed |
| 002 | Persistence (V0.3) before planner (V0.4) | A deadline requires demoing planning sooner — and record that it was a presentation decision |
| 003 | Postgres `SKIP LOCKED`, not Celery/Redis | Independent worker scaling, or scheduled tasks |
| 004 | Modular monolith, not microservices | A component needs independent scaling or fault isolation |
| 005 | Gemini behind an `LLMProvider` protocol | Free-tier limits block development |
| 006 | No database at V0.1 | V0.2 needs cross-request state |
| 007 | 10 docs written, 14 stubbed | A stub's subject starts influencing implementation |
| 008 | Embeddings at 1536 dims, re-normalised | Measured recall@1536 materially worse than 3072 |

### Smaller decisions

**No `src/` directories until they hold code.**
*Context:* the brief's suggested layout lists ten `src/` subdirectories.
*Decision:* create each at the milestone that fills it.
*Why:* an empty `agents/` directory claims progress that does not exist — the structural form of
the placeholder-documentation problem (ADR-007).
*Reconsider if:* never; a directory costs nothing to create when it is actually needed.

**Tests never touch the network.**
*Context:* free-tier Gemini is rate-limited and non-deterministic. (Written here as "~15 RPM";
the measured picture turned out to be four different shapes depending on model and call type —
`docs/21-technology-baseline.md` is canonical.)
*Decision:* `FakeProvider` in all unit and integration tests; one live smoke test, skipped
without an API key.
*Why:* network tests would be slow, flaky, rate-limited and would fail in CI. Locked as N-14.
*Reconsider if:* never for unit tests. A separate, opt-in live suite may grow at V1.0.

**No `Co-Authored-By` trailer on commits.**
*Context:* Anshul's explicit instruction, reversing an earlier choice to keep it.
*Decision:* omit at commit time rather than adding and stripping afterwards.

**GitHub repository private until v0.1.**
*Context:* the repo becomes public evidence for placements.
*Decision:* private now; public when V0.1 runs with passing tests.
*Why:* a visitor's first impression should be a working project, not an empty `src/`.

---

## 2026-09-03 — Session 3 (V0.2)

No new ADRs; V0.2 implements existing decisions. Smaller decisions worth recording:

**`Tool` is an ABC while `LLMProvider` is a Protocol.**
*Context:* apparent inconsistency in how the two abstractions are expressed.
*Decision:* Protocol for providers, ABC for tools.
*Why:* providers share a shape (nothing inherited → structural typing); tools share behaviour
that must not be skipped (validation, timeouts). Placing that in a concrete `execute()` means a
tool has no opportunity to omit it.
*Reconsider if:* a tool ever legitimately needs to bypass `execute()` — which would itself be
evidence the base class is wrong.

**Tool failures are returned as data, not raised.**
*Decision:* `Tool.execute()` never raises; every path returns a `ToolOutcome`.
*Why:* the model must be told what went wrong to correct itself. An exception unwinds the loop
and turns a recoverable mistake into a failed request. Termination is still guaranteed by the
iteration cap, not by failures propagating.

**`WRITE`/`DESTRUCTIVE` permissions refused by the registry.**
*Decision:* `ToolRegistry.register()` raises on them.
*Why:* naming the boundary in an enum and enforcing it in code is more honest than leaving it
unmentioned, and makes crossing it a deliberate act rather than an oversight.
*Reconsider if:* the human-approval workflow in `docs/13-security.md` is built.

**Default model changed to `gemini-3.5-flash-lite`.**
*Context:* free-tier quota measured at **20 requests/day per model**, not 15/minute.
*Decision:* lite for development; `gemini-3.5-flash` reserved for demos.
*Why:* quota is per model, so this genuinely doubles the daily budget.
*Reconsider if:* lite's tool-selection quality proves materially worse — check before V0.7.

---

## 2026-09-03 — Session 4 (V0.3)

No new ADRs. Implementation decisions:

**Run row written before execution, updated after.**
*Why:* a crash mid-run still leaves evidence it was attempted — exactly the runs worth
investigating. Writing afterwards would lose them entirely.

**Execution happens outside any database transaction.**
*Why:* an LLM call takes seconds and a transaction holds a pooled connection for its lifetime.
With `pool_size=5`, six concurrent goals would deadlock waiting for connections while doing
nothing but network I/O. Three short transactions with the slow work between them.

**`run_id` denormalised onto `llm_calls` and `tool_calls`.**
*Why:* trace assembly is the hottest read in the system; this makes it one indexed filter per
table rather than a walk through `runs → steps → children`.
*Cost:* a redundant column that must stay consistent.

**Persistence is optional; the app runs without a database.**
*Why:* "every milestone is runnable" has to survive V0.3. Without `AMOS_DATABASE_URL` the app
serves goals and `/v1/runs/{id}` returns 503 with an explanation. 118 of 136 tests pass with no
container.
*Reconsider if:* a later milestone genuinely cannot function without persistence — V0.8's worker
probably cannot, and that should be an explicit decision rather than a drift.

**Podman rather than Docker, for now.**
*Context:* neither installed; pgvector is not in Arch's repos, so a container is the clean path.
*Decision:* podman — rootless, no daemon, no group membership requiring a re-login.
*Consequence:* `compose.yaml` is written for both but **verified only on podman**.
`docs/18-deployment.md` states this rather than implying Docker was tested.

---

## 2026-09-05 — Session 5 (V0.4)

No new ADRs; V0.4 implements the orchestration model from `docs/04-domain-model.md`.

**Only `orchestration/state.py` may change a task's state.**
*Why:* the invariant "LLMs handle uncertainty, software handles guarantees" is only real if there
is exactly one enforcement point. `assert_transition` raises on anything the table forbids.
*Reconsider if:* never. If a state change needs to happen elsewhere, add the transition to the
table — do not bypass it.

**A retry returns the task to `READY`, not to a retry-specific state.**
*Why:* one code path for "about to run", so a retried attempt cannot diverge from a first one.

**`depends_on` as a Postgres `UUID[]`, not a join table.**
*Why:* every read loads a run's whole task graph anyway, so the join buys nothing and costs a
table on the hottest path.
*Reconsider if:* dependencies ever need attributes of their own (a type, a condition).

**Synthesis skipped for single-task plans and for total failures.**
*Why:* a single task's answer already is the answer; restating it costs a call from a 20/day
budget. Nothing to synthesise when nothing succeeded.

**Alembic metadata naming convention.**
*Why:* without it, autogenerate produced an unnamed FK and an irreversible migration.
*Consequence:* required rebaselining, which rewrote V0.3's migration — acceptable only because
it had run on one machine. Recorded in `bugs-log.md`.

**`AMOS_PLANNING_ENABLED` falls back to the V0.2 single-shot agent.**
*Why:* not every goal needs decomposition, and planning costs 3-8× the calls. On a 20/day quota
that is the difference between six goals and two.

---

> **Gap closed 2026-09-13.** This file stopped here for six milestones. The decisions of
> V0.5–V1.0 were written down — under "Architecture Decisions" in `session-log.md` — but never
> rolled into the chronological index that is supposed to be their home. The entries below are
> that backfill, dated to the session that made each. The lesson is the one the index exists to
> teach: a decision recorded only in a narrative is a decision nobody will find.

## 2026-09-05 — Session 6 (V0.5, retrieval)

**Re-normalise every truncated embedding at the provider boundary, with a test.**
*Why:* MRL truncation breaks L2 normalisation, and cosine distance over un-normalised vectors
returns wrong rankings **without raising**. A documented intention would not have caught it; a
test at the boundary does. ADR-008.
*Reconsider if:* never while truncation is used.

**Retrieval is a Tool, not a pipeline stage.**
*Why:* the agent *chooses* to retrieve, so goals needing no corpus do not pay for an embedding
call — and retrieval inherits argument validation, timeouts and trace visibility for free.
*Reconsider if:* retrieval becomes mandatory for correctness rather than useful for grounding.

**Empty retrieval returns a refusal instruction, never an empty list.**
*Why:* given an empty list the model answers from its own memory and presents it as grounded.
The failure is invisible precisely because the output looks the same.

**Heading-aware chunking, with the heading prepended to each of its chunks.**
*Why:* a chunk that has lost its section title has lost what the question will be asked about.

**Ingestion commits per document, not per run.**
*Why:* one transaction around 300 chunks reads as "atomic ingestion" and is really "lose
everything on any error" — which is exactly what a 429 partway through did.
*Consequence:* recorded in `bugs-log.md`; transaction boundaries follow units of *useful work*.

**Ground truth is multi-source; strict and lenient recall are both reported, permanently.**
*Why:* reporting one number invites picking the flattering one later.

## 2026-09-09 — Session 7 (V0.6, memory)

**Facts are relational-first, with vectors as a secondary index.**
*Why:* exact recall is a key lookup, contradictions need ordering, and provenance is a join.
Similarity search does none of the three and will occasionally return a similar fact about
someone else with high confidence.
*Reconsider if:* facts stop having stable subject keys.

**Contradiction resolution is newest-wins, in code. Superseded rows are kept.**
*Why:* a rule, not a judgement — so the outcome is reproducible and the history stays auditable.

**Episodic memory adds no table.**
*Why:* an episode *is* a run. A separate `episodes` table would duplicate goal, status, tokens
and timings to add two columns. It is `runs.lesson` + `runs.goal_embedding`.

**The lesson is derived, not generated.**
*Why:* an LLM call per run to restate facts already recorded would cost ~5% of the daily quota
to add nothing.

**Conversation memory deliberately not built.**
*Why:* there is no multi-turn API to remember *for*. Every `/v1/goals` call is independent.
*Reconsider if:* a conversational endpoint is added. Two of the five memory kinds in
`docs/09-memory-architecture.md` remain unbuilt, and this is one of them.

## 2026-09-09 — Session 7 (V0.7, multi-agent)

**Specialisation is a tool allowlist enforced by construction, not a prompt.**
*Why:* a prompt saying "you have no calculator" is a request; a registry that does not contain
`calculator` is a guarantee. `AgentSpec.registry_from()` builds a filtered registry, so a
disallowed tool returns `NOT_FOUND` because it does not exist.
*Reconsider if:* never — this is the golden rule applied to agents.

**Allowlists are disjoint.**
*Why:* overlapping capability makes routing arbitrary, because either agent could do the work,
and a routing accuracy number then measures nothing.

**The critic has no tools.**
*Why:* a critic that can fetch new sources can always find something justifying what it already
concluded. Judging only what it was given is what makes the judgement mean anything.

**A broken critic accepts.**
*Why:* it is a quality gate, not a correctness requirement. Failing closed here would convert a
critic outage into a total outage.

**The revise loop is bounded in code, and unresolved objections are attached to the answer with
confidence downgraded — never hidden.**

## 2026-09-09 — Session 7 (V0.8, async)

**Claim at run level, not task level.**
*Why:* a run is what a client submits and polls, and a run's internal task concurrency is
already handled by `asyncio.gather` inside one worker. Distributing tasks would mean
distributing the executor.
*Consequence:* **corrected V0.4's speculative `tasks.claimed_at` column and its partial index,
which were removed rather than carried.** The reasoning that added them ("adding a column later
to a populated table is a migration") was sound; the granularity was wrong. Carrying schema that
documents an abandoned plan is worse than the migration it saves.
*Reconsider if:* one run's tasks ever need to span workers.

**Polling, not `LISTEN`/`NOTIFY`.**
*Why:* a second mechanism — dedicated connection, reconnect handling, notifications lost when
nobody is listening — to save latency nobody is measuring.
*Reconsider if:* poll latency becomes user-visible.

**At-least-once, stated explicitly.**
*Why:* exactly-once is not available. The mitigation is idempotent work, and `remember_fact` is
recorded as a real gap rather than a solved problem — it is harmless today *by luck*, because
supersession makes a duplicate store a no-op.
*Reconsider if:* never the guarantee; the gap is scheduled for V1.1.

**The worker swallows every exception, bounded by an attempt ceiling.**
*Why:* a worker that dies on one bad run turns a poison message into a total outage.

## 2026-09-09 — Session 7 (V0.9 observability, V1.0 evaluation)

**Goal text is not a span attribute by default.**
*Why:* spans are shipped, stored and searchable. The safe direction has to be the default,
because an opt-*out* ships user content from every deployment that forgot to set it.

**Metric labels come from a closed allowlist.**
*Why:* an unbounded label is one time series per value, which destroys a metrics backend. An
allowlist fails closed on the label nobody anticipated; a blocklist fails open.

**Deterministic and judged metrics are reported separately and never averaged.**
*Why:* the judge shares a model family, training data and blind spots with the system it judges.
Averaging it into deterministic scores launders weak evidence into strong-looking numbers.
*Consequence:* only the deterministic metrics gate CI.

**A rate limit is `unmeasurable`, not a failure.**
*Why:* otherwise part of every quality score is a measurement of the free tier.

**CI runs the suite with and without a database, migrations in both directions, and with no API
key.**
*Why:* three claims the project makes about itself — optional persistence, reversible
migrations, no test touches the network (N-14) — become enforced rather than documented.

## 2026-09-13 — Coherence audit

**ADR-009 — a task-level timeout, and the ordering of the bounds.** Full record in
`docs/03-architecture-decisions.md`. In short: every bound beneath a task is per *call*, a task
is a *loop* over those, so bounded parts did not make a bounded whole and `TaskState.TIMED_OUT`
was unreachable for six milestones. The decision is the ordering — per-call bounds < task
timeout < worker visibility timeout — more than the number.

### Smaller decisions

**`AMOS_ASYNC_ENABLED` is honoured in code, and its default stays `false`.**
*Context:* the setting was read by nothing while three documents instructed readers to set it.
*Why not flip the default to `true` to preserve behaviour:* because those three documents
already assume opt-in, and queueing with no worker running leaves runs QUEUED forever. Making
the documented mental model true was worth more than preserving an undocumented one.

**A test per defect *class*, not per defect.**
*Context:* three of the six coherence defects already had a narrow test nearby that passed. The
version test checked only for the *current* version appearing as a literal, so a stale "V0.7"
sailed through; the state-machine tests were exhaustive over the transition *table*, which was
correct, while nothing produced one of its states.
*Decision:* every fix ships a test that would catch the next instance of its kind — every import
declared, every setting read, every instrument written to, every state reachable, every setting
documented.
*Why:* a test that names the bug you just fixed protects against a bug that has already been
fixed.
