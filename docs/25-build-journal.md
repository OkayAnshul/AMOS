# 25 — Build Journal

**How AMOS was actually built, from an empty directory to a working system.**

This is the construction narrative: what was used, how each piece was built, what was decided,
why, and what turned out to be wrong. It is deliberately chronological, because the *order*
things were built in is itself one of the design decisions.

Companion documents:
- [`24-study-plan.md`](24-study-plan.md) — what to **study**, tier by tier
- [`03-architecture-decisions.md`](03-architecture-decisions.md) — the formal ADRs
- [`../engineering/bugs-log.md`](../engineering/bugs-log.md) — every defect and its lesson
- [`../engineering/session-log.md`](../engineering/session-log.md) — per-session detail

---

## Chapter 0 — The starting point

**2026-09-02.** `/home/anshul/0-Structure/1-Work/AMOS` was an empty directory. Not a git repo.

The first thing done was not writing code or picking a framework. It was **measuring the
environment**, because half the decisions that follow depend on facts about the machine:

```
Python      3.14.4      only version installed
Disk        9.4 GB free, 96% full, / and /home on the same partition
Docker      not installed
PostgreSQL  not installed
Node        25.9.0
```

That disk figure was a genuine architectural input — the machine was one large download away from
failing to boot, and every container image is a few hundred megabytes. It was cleared to 119 GB
before anything was built, which removed the constraint. **One of the three arguments originally
made for pgvector over Qdrant was disk pressure, and it evaporated.** The decision did not change,
but ADR-001 records that a reason disappeared rather than quietly keeping it. A justification you
no longer believe is worse than one you never made.

### Verifying instead of recalling

The second thing done was checking library facts against primary sources. Three assumptions were
wrong:

| Assumed | Actually |
|---|---|
| `google-generativeai` is the SDK | **Deprecated.** `google-genai` 2.21.0 is current |
| Models are `gemini-1.5-*` | **`gemini-3.5-flash`** and the 3.x line |
| pgvector indexes any dimension | **2000 max** for HNSW — and the embedding model defaults to 3072 |

The third would have surfaced at V0.5, *after* embedding an entire corpus at an unindexable
dimension. Checking one README in Phase 0 saved re-embedding everything four milestones later.

**The rule adopted:** never write a version number, model ID or API signature from memory. This
recurs throughout — by V0.5 the tally of documented-or-assumed facts contradicted by the live API
reached six.

---

## Chapter 1 — Phase 0: architecture before code

**No application code was written.** The output was 10 documents, 14 honest stubs, and six
engineering logs.

### Three places the original brief was wrong

The project brief specified a system; three parts of it did not survive review.

**1. Qdrant → pgvector (ADR-001).** The brief named Qdrant. Qdrant's advantages appear around
millions of vectors; its cost appears on day one. With a separate vector database, a chunk's row
lives in Postgres and its embedding lives in Qdrant, so every write is a distributed write with no
shared transaction. Postgres commits, Qdrant fails, and the index now disagrees with the source of
truth — a real problem, voluntarily created, to solve a scale problem the project does not have.

**2. Reliability last → persistence third (ADR-002).** The brief sequenced tools → planner → RAG →
memory → *reliability*. But a planner's output **is** state. The moment a goal decomposes into a
task graph, you have distributed task state — you just have it in memory, where it cannot be
inspected, replayed or resumed. Persistence moved to V0.3, ahead of the planner.

**3. 24 documents upfront → 10 written, 14 stubbed (ADR-007).** The brief asked for 24 documents
before implementation and *separately* forbade placeholder documentation. Both cannot hold. A
chunking-strategy document written before anything is embedded specifies chunk size and top-k as
guesses that then read as decisions, get cited, and constrain later work for no reason.

Each stub says which milestone will write it and what fact it waits on. `10-rag-architecture.md`
sat as a stub for five milestones and was written at V0.5 **with measured numbers**.

### The method, stated once

Every technology answers "what problem does this solve *here*?" and carries an explicit
**Reconsider if**. A decision without a falsifiable reversal condition is a preference wearing a
decision's clothes.

---

## Chapter 2 — V0.1: the foundation, and four seams

**Goal:** a typed, tested LLM service. **Deliberately no database.**

That last part is the whole lesson of the milestone. Nothing in V0.1 needs to survive a restart —
a goal comes in, an answer goes out. Adding Postgres would mean Docker, a schema, migrations and
connection lifecycle: real complexity against no requirement, and it would make V0.1 impossible to
run without infrastructure. It is easy to state and uncomfortable to follow, because a database
feels like seriousness.

### What was built

```
Client → FastAPI → GroundedAgent → LLMProvider → Gemini
                        ↓
                  validate-and-repair (bounded)
```

| Piece | Why |
|---|---|
| `LLMProvider` **Protocol** | Structural typing — a fake satisfies it without inheritance |
| `AgentResponse` Pydantic model | The model must produce *structure*, not prose |
| Validate-and-repair loop | Provider schemas make valid output likely, not certain |
| Typed error hierarchy | One status-code mapping table, not scattered `except` blocks |
| Request id in every log line | Costs nothing now; becomes the trace id at V0.9 |

### The four seams

This is the part worth understanding, because it is what made V0.2–V0.5 additions rather than
rewrites:

| Seam | Justified by a V0.1 need | Later becomes |
|---|---|---|
| `LLMProvider` protocol | tests must not hit a rate-limited API | other providers |
| `AgentResult` + `LLMCallRecord` | structured logging needs the fields | the `llm_calls` **table rows** |
| validate-and-repair loop | malformed JSON is a real failure | tool-arg validation, plan validation |
| request-id threading | debugging needs it on day one | OpenTelemetry `trace_id` |

**The load-bearing claim: every seam is paid for by a V0.1 need.** None is speculative. That is
the difference between designing for evolution and over-engineering — and V0.3 tested it directly.

### Two bugs, and why they matter

**`log_event() got multiple values for argument 'message'`** — every API error path was broken.
The happy path was fine, so a manual demo would have shipped it. Caught only by error-path
integration tests. *Error paths need testing as deliberately as success paths.*

**`python -m amos` → `ModuleNotFoundError`, with 41 tests passing.** A `src/` layout needs an
explicit hatchling editable target; without it pip reported the package installed while nothing
could import it. Tests passed because pytest's own `pythonpath` bypassed the broken mechanism.
***A green test suite does not prove the application starts.*** This is why "run the demo" is in
the Definition of Done.

---

## Chapter 3 — V0.2: tools, and the security boundary

**Goal:** the agent selects and executes tools, with the system enforcing what it may touch.

### Protocol vs ABC — the decision worth internalising

`LLMProvider` is a Protocol. `Tool` is an **abstract base class**. That is not inconsistency:

- Providers share a **shape**. Nothing is inherited, so structural typing is right.
- Tools share **behaviour** that must not be skipped: every one must validate its arguments and
  honour its timeout.

That behaviour lives in a concrete `Tool.execute()`; subclasses implement only `_run()`. So **a
tool cannot opt out of validation or timeouts — it is given no opportunity to.** If each author
had to remember, one eventually would not, and that tool would be the vulnerability.

> Shared shape → Protocol. Shared behaviour that must not be skipped → ABC.

### Schemas from one source

`Tool.spec()` derives the model-facing declaration from `input_schema.model_json_schema()`, and
the same Pydantic model validates incoming arguments. Never hand-written. A hand-maintained
declaration drifts from the validator, and then the model is told one thing while the code
enforces another — the model supplies what it was told and gets rejected, with no way to discover
why.

### Failures are data, not exceptions

`Tool.execute()` **never raises**. Every path returns a `ToolOutcome`, including `not_found` for a
hallucinated tool name. A hallucinated tool is *expected* behaviour, not exceptional — the model
is told what exists and sometimes invents something else. The error message lists the real tools
so it can recover. Raising would turn a recoverable mistake into a failed request.

The agent still cannot be trapped: a failed attempt consumes an iteration, and the loop cap
converges regardless.

### Security, built as code rather than instructions

The governing principle: **security is enforced in code that never reads model output.**

| Tool | Attack | Defence |
|---|---|---|
| `calculator` | code execution via `eval` | AST walk with an explicit node allowlist — `Call`, `Name`, `Attribute`, `Subscript` all rejected |
| `calculator` | `9**9**9` blocks the event loop | exponent capped at 64 **before** evaluation (a timeout cannot rescue a blocked loop) |
| `read_file` | `../../etc/passwd` | resolve **then** check containment |
| `read_file` | symlink escape | `Path.resolve()` follows symlinks; the check is on the *resolved* path |
| `http_get` | SSRF | https-only, host **allowlist**, private-IP rejection, no redirect following |

Two subtleties worth the space:

**Resolve, then check.** Validating the path string before resolution is the classic mistake — a
symlink named `innocent.md` contains no `..`, no absolute path, nothing suspicious. Only
resolution reveals where it points.

**Allowlist, not blocklist.** A blocklist must anticipate every dangerous target and fails *open*
when it misses one. An allowlist names the safe ones and fails *closed*. Also: the subdomain check
uses a leading dot, because a naive `endswith("github.com")` accepts `evil-github.com`.

On prompt injection, the honest position: the system prompt does tell the model to treat tool
output as data. **That is not a control.** What actually holds is that the registry is fixed at
startup, each tool enforces its own boundary regardless of why it was called, and write tools
cannot be registered at all. The test asserts the interesting version — *assume the model is fully
compromised and emits the attacker's call* — and the system still refuses.

### Where the docstring lied

`_finalise()` existed because its docstring said Gemini could not accept a response schema and
tool declarations together, annotated **"Verified against the API."** It had never been tested. It
can. That cost one wasted API call per goal — a third of the daily budget.

**The damage was not the wrong belief. It was writing it down as verified**, which stopped the
next reader — me — from questioning it.

---

## Chapter 4 — V0.3: persistence, and testing the bet

**Goal:** every run durable and inspectable. The hinge milestone.

### Three transactions, not one

```
1. check idempotency        (transaction)
2. create the run row       (transaction)
   ── execute the agent ──   NO transaction
3. record the outcome       (transaction)
```

Two decisions in that shape:

**The run row is written *before* execution.** A crash then still leaves evidence the run was
attempted — precisely the runs worth investigating. Writing afterwards loses them entirely.

**Execution happens outside any transaction.** An LLM call takes seconds and a transaction holds a
pooled connection for its lifetime. With `pool_size=5`, six concurrent goals would deadlock
waiting for connections while doing nothing but network I/O.

### Did the seams hold?

This was the milestone that tested V0.1 and V0.2's central bet. The answer:
`_add_trace_rows()` is a **mechanical field-by-field copy**. No field had to be derived, inferred
or restructured.

**One gap, stated rather than glossed:** `ToolOutcome` recorded a tool's *result* but not the
*arguments* it was called with, so the first working trace showed outputs with no inputs. The seam
was right in shape and ~95% right in content. "It worked perfectly" would have been the less
useful record.

### Three bugs with transferable lessons

**`postgres:18` exited(1) while `compose up` returned 0.** PostgreSQL 18 images changed the volume
convention to `/var/lib/postgresql` (not `/data`) so `pg_upgrade --link` works across one mount
boundary. Every tutorial still shows the old path. ***"The container started" is not "the service
is running."*** Healthchecks belong in the first version of a compose file.

**10 of 11 database tests: "attached to a different loop."** The engine fixture was session-scoped
but pytest-asyncio gives each test its own event loop, and asyncpg connections belong to the loop
that created them. *When the first test passes and the rest fail identically, suspect the
fixtures, not the subject.*

**Adding `AMOS_DATABASE_URL` to `.env` broke 12 passing tests.** `Settings` declares
`env_file=".env"`, so tests inherited the developer's machine. Latent since V0.1; it only surfaced
when `.env` gained a setting that changed behaviour. ***A test that reads `.env` depends on the
machine it runs on.***

---

## Chapter 5 — V0.4: orchestration, and one enforcement point

**Goal:** decompose a goal into a task DAG with deterministic state.

### The central rule

> **Only `orchestration/state.py` moves a task between states, and it raises on anything the
> transition table does not permit.**

This is the invariant "LLMs handle uncertainty, software handles guarantees" made executable. If a
model could mark its own task `SUCCEEDED`, "this task completed" would mean "the model said so" —
and every guarantee in the system would be advisory.

| The model decides | Code decides |
|---|---|
| how to decompose the goal | whether a task may change state |
| what each task should say | whether a retry is permitted |
| which tool to use | what happens to a failed task's dependents |

### Three design points that are easy to get wrong

**A retry returns the task to `READY`**, not to a retry-specific state. One code path for "about to
run" means a retried attempt cannot diverge from a first one — no separate branch to forget.

**`FAILED`/`TIMED_OUT` stay distinct from `PERMANENTLY_FAILED`.** The transient states are where
the retry decision happens. Collapsing them would make "was this retried, and how often?"
unanswerable from the data.

**Illegal transitions raise, never warn.** A silently accepted illegal transition means the
recorded state no longer describes reality, and every decision built on it afterwards is wrong —
including the trace you would use to debug it.

Tested with all 11 legal transitions **and all 53 illegal ones**, plus a test asserting the
module's table and the test's expectations agree so they cannot drift.

### Validate before persisting

A cyclic plan reaching the database would be a run that can never complete, holding rows that look
live forever. Cycle detection is *iterative* DFS with an explicit stack — a confused plan should be
rejected, not crash the process — and returns the actual cycle so the repair prompt can name it.

### Why jitter is not decoration

Five tasks fail at the same instant. Without jitter all five wait exactly 0.5s, retry together,
fail together, wait exactly 1.0s, and collide again — forever, in lockstep. Backoff spaces
attempts in *time* but not across *tasks*, so it recreates the burst that caused the failure.

There is a test beyond the bounds check — `test_jitter_actually_varies` — because "jitter" could
otherwise be implemented as a constant and pass everything else.

### The irreversible migration

`alembic upgrade` worked perfectly. `alembic downgrade` failed:
`Can't emit DROP CONSTRAINT ... it has no name`. The migration applied and **could not be
reversed**, discovered only because reversing it is in the Definition of Done.

Fix: a `naming_convention` on the metadata so every constraint is droppable by name. That required
a fresh baseline, which **squashed V0.3's migration** — a history rewrite of a shipped artifact,
safe *only* because it had run on exactly one machine. Once anyone else has applied a migration,
that option is gone.

---

## Chapter 6 — V0.5: retrieval, and the metric that misled

**Goal:** a retrieval pipeline with a *measured* recall figure, not a vector database with a claim
attached.

### The trap that fails silently

`gemini-embedding-001` returns 3072 dimensions; pgvector's HNSW index handles at most 2000. So the
default output is unindexable, and MRL truncation to 1536 is required. Measured against the live
API:

```
3072 dims → L2 norm 1.000000
1536 dims → L2 norm 0.686517     ← 31% off unit length
```

Cosine distance assumes unit vectors. pgvector's `vector_cosine_ops` **does not raise, does not
warn, and returns wrong rankings.** Retrieval quietly degrades and every metric still reports a
number. Every truncated embedding is re-normalised at the boundary, with a test.

### Chunking decides what can be found

Heading-aware first, size-based only as fallback. A Markdown section is a coherent unit of
meaning; an arbitrary 1000-character window is not.

**The heading is prepended to every chunk of its section.** Without it, a chunk from the middle of
"Why pgvector, not Qdrant" loses the only words that say what it is about — which are exactly the
words a question about it would use.

### The metric that lied, and what fixed it

The first golden set had **one** expected source per question. It scored `recall@1 = 50%`, which
looked like bad retrieval.

Every single miss had retrieved an `interview/*.md` document. Those are written as Q&A, so for a
*question* query they are frequently the best semantic match — and they **genuinely answer the
question**. Retrieval was not failing; the labels assumed one correct source where the corpus has
real redundancy.

Ground truth was widened, and recall@1 went 50% → 91.7%.

**That is exactly how a metric gets massaged until it looks good.** Three guards keep it honest:

1. Both figures reported permanently — `strict` still counts only the primary source
2. A source is added only when it genuinely answers the question, never to raise a number
3. The rule lives in `GoldenQuestion`'s docstring, so the next person inherits the constraint

**The real lesson: MRR was the honest signal all along** — 0.917 at k=1, meaning the right document
was almost always rank 1, while single-label recall said 50%. *When two metrics disagree that
sharply, suspect the measurement before the system.*

| k | recall (any valid source) | strict (primary only) | MRR |
|---|---|---|---|
| 1 | 91.7% | 50.0% | 0.917 |
| 3 | 100% | 83.3% | 0.958 |
| 5 | 100% | 91.7% | 0.958 |

### Transaction boundaries follow useful work

The first ingest hit a rate limit and **rolled back all 300 embedded chunks**. The rollback was
correct; the boundary was wrong — the whole corpus sat in one `session_scope`.

***Transaction boundaries should follow units of useful work, not units of code.*** "All 300
chunks atomically" reads as rigour and means "lose everything on any failure." Now it commits per
document.

No unit test could have caught it: `FakeEmbeddings` cannot rate-limit, and a 3-document fixture
never runs long enough to fail partway. **Fakes cannot test what they do not model** — the same
lesson as V0.2's `thought_signature`, in a new costume.

---

## The method: how decisions were actually made

Every significant decision followed the same shape:

```
1. What problem does this solve HERE?          (not "is it popular")
2. What are the real options?                   (including doing nothing)
3. What does each cost, concretely?             (services, failure modes, complexity)
4. Decide, and write the tradeoff down
5. State the Reconsider if                      (what would make this wrong)
6. Record when a reason later evaporates
```

Step 5 is what separates a decision from a preference. Step 6 happened once, visibly: ADR-001's
disk-pressure argument disappeared when the disk was cleared, and the ADR says so.

### The bias that had to be actively resisted

Every milestone offered a chance to add something impressive:

| Tempting | Chosen | Why |
|---|---|---|
| Qdrant | pgvector | dual-write consistency cost, no scale benefit |
| Celery + Redis | Postgres `SKIP LOCKED` | one system already present solves it |
| Microservices | modular monolith | one developer, one machine, no ownership boundaries |
| Kubernetes | nothing | there is no deployment to orchestrate |
| LangChain | hand-written orchestration | the orchestration layer *is* the thing being learned |
| A database at V0.1 | none | nothing needed to survive a restart |

None of these are permanent judgments about the technologies. They are judgments about this
system, at this size, with these constraints — which is what every ADR's *Reconsider if* records.

---

## Everything that was wrong

Kept in one place because the corrections are more instructive than the successes.

| # | Believed | Reality | Cost if unfound |
|---|---|---|---|
| 1 | `google-generativeai` is the SDK | deprecated | building on a dead library |
| 2 | models are `gemini-1.5-*` | 3.x line | runtime failure |
| 3 | pgvector indexes any dimension | 2000 max | re-embed the corpus at V0.5 |
| 4 | free tier ~15 requests/minute | **20 per day** | budget planning wrong by 100× |
| 5 | `gemini-2.5-flash` is a fallback | 404, not served | broken fallback path |
| 6 | tools and `response_schema` conflict | they combine | 1 wasted call per goal, forever |
| 7 | truncation preserves normalisation | 0.686517 | silently degraded retrieval |
| 8 | embedding quota resembles chat quota | 100/min, counts contents | failed ingests |
| 9 | one transaction is safer | it discards partial work | lost 300 chunks |
| 10 | a green suite means it runs | pytest's path masked a broken install | shipping a package that cannot import |
| 11 | the memory tools were wired in | a silent patch no-op meant they were never registered | a documented feature the agent cannot reach |
| 12 | supersede-then-insert is the safe order | the foreign key makes it impossible | — caught immediately |
| 13 | the test fake was deterministic | `hash()` is randomised per process | flaky retrieval tests since V0.5 |
| 14 | package metadata gives the current version | it gives the last *build's* version | `/health` reporting a stale version |
| 15 | `recall_past_runs` belongs to the analyst | it is a lookup, so it belongs to the researcher | the routing measurement caught it |
| 16 | `tasks.claimed_at` would be needed at V0.8 | claiming is per-run; the granularity was wrong | schema documenting an abandoned plan |
| 17 | models and migrations were in sync | five columns had diverged for two milestones | invisible until something used the ORM |
| 18 | `score = -1` works as a sentinel | the field's own bound rejects it | a magic number that gets averaged by accident |
| 19 | a rate-limited case is a failure | it is *unmeasurable* | the score partly measures the free tier |
| 20 | the refusal case failed | the **detector** failed; the system refused correctly | reporting a brittle metric as a real finding |

Six of these came from documentation being wrong or untested. **Documentation is not behaviour.**

---

## What it costs to run

| Resource | Reality |
|---|---|
| `generateContent` | **20 requests/day per model** — a 3-task plan costs 8 |
| `embed_content` | **100/minute**, counts *contents* not requests |
| Money | £0 — everything on free tiers |
| Disk | ~1 GB (container image + corpus) |
| Services | one Postgres container |

The daily quota is not an inconvenience; it shaped the code. Synthesis is skipped for single-task
plans, `AMOS_PLANNING_ENABLED=false` exists, and tests never touch the network — all because
20/day is roughly ten goals.

---

## Where the numbers come from

Nothing in this journal is estimated:

| Claim | Source |
|---|---|
| L2 norm 0.686517 | live API, `experiments-log.md` |
| 20 requests/day | the 429 body's `quotaId` |
| recall@5 100%, MRR 0.958 | `python -m amos.rag.cli evaluate 5` |
| 53 illegal transitions | `test_state.py`, parametrised |
| 314 tests | `pytest -q` |

Anything that cannot be reproduced by a command in this repository is not claimed. That rule is
also what keeps [`22-resume-evidence.md`](22-resume-evidence.md) honest.

---

## Chapter 7 — V0.6: memory, and resisting the obvious answer

**Goal:** facts that survive a restart, and past runs that can be found again.

The code was the easy part. The decision was **which store each kind of memory belongs in**, and
the reflexive answer — embed everything, search by similarity — is wrong.

### Why facts are relational, not vectors

| | Why similarity fails |
|---|---|
| "What is the user's name?" | returns the *most similar* fact; in a store with several names, sometimes the wrong person's. No threshold fixes it — the failure is semantic |
| A fact that changed | a vector index has no notion of *superseded*; it returns old and new, ranked by distance, with nothing to say which is true |
| "Where did this come from?" | provenance is a foreign key, not a nearest neighbour |

So: `subject` is a normalised key for exact lookup and contradiction detection; the embedding is a
**secondary** index for questions that arrive without a key. `recall_facts` tries exact first,
always.

Contradiction resolution is **newest-wins, decided in code**. Superseded rows are kept, so a
changed fact stays auditable and a bad write stays recoverable.

### Episodic memory has no table

An episode **is** a run. Goal, outcome, tokens, duration — `runs` already stores all of it. A
separate table would duplicate every column to add an embedding and a lesson.

So episodic memory is two columns plus queries: **an index on an existing store, not a new store.**
Before adding somewhere to put things, check whether the thing already has a home.

One consequence of an earlier decision surfaced here: V0.3 writes the run row *before* executing,
so by the time a goal asks "have I done this before?", it is already the most similar past goal.
`exclude_run_id` exists because otherwise episodic recall returns the question as its own answer.

### The bug that matters most in this project so far

The first cross-session demo failed. Session 1 replied *"I have noted that your preferred backend
language is Python."* The `memories` table was empty.

My first diagnosis was **wrong**: "the model chose badly, the tool descriptions overlap." Plausible,
and it fit the symptom.

The startup log said otherwise:

```
"tools": ["calculator", "http_get", "read_file", "search_knowledge"]
```

Four tools, not seven. The memory tools had been written, tested and documented — and **never
registered**. A `str.replace` patch to `build_registry` had silently no-opped because formatting
had changed the text it matched. The model could not have called a tool it was never given.

Three lessons, in increasing order of importance:

1. **A silent `str.replace` no-op is a class of bug** — the third occurrence here. Patching by
   pattern must fail loudly when the pattern is missing.
2. **Unit tests verify components; nothing verified they were connected.** 344 tests passed with
   the feature completely unreachable. `test_tool_wiring.py` now asserts exactly which tools the
   agent receives. *Wiring is a behaviour and needs a test.*
3. **When a symptom is consistent with "the model did something odd", check the deterministic
   explanation first.** LLM systems make it far too easy to blame the model, because that
   explanation is unfalsifiable enough to be comfortable. The evidence was one `grep` away.

### And a comment that defended an impossible design

`remember()` originally superseded the old row *before* inserting the new one, with a comment
explaining why that ordering was necessary. Postgres rejected it: `superseded_by` is a foreign key,
and the new row did not exist yet.

The comment was wrong twice — the ordering it defended was impossible, and the danger it warned
about was not real (both statements share a transaction, so no other transaction sees the
intermediate state). **A confident comment justifying an impossible design is worse than no
comment.** Same shape as V0.2's `"Verified against the API"` docstring that had never been verified.

---

## Chapter 8 — V0.7: making "multi-agent" an honest word

**Goal:** specialised agents that genuinely differ, communicating in structured messages, with a
critic gating the output.

Note where this sits: **"multi-agent" is not claimed anywhere before V0.7.** Six milestones of
`22-resume-evidence.md` say "not built" on that row, because one agent with three prompts is not
a multi-agent system and an interviewer will ask exactly that.

### The property that makes it real

Agents differ in **capability, enforced by construction**. Each is handed a `ToolRegistry`
containing only its allowlisted tools, so a Researcher asking for `calculator` gets `NOT_FOUND`
through the machinery V0.2 already built — no new enforcement path.

| Agent | Tools | Cannot |
|---|---|---|
| Researcher | search, fetch, read, recall | compute |
| Analyst | calculator | search, fetch, recall |
| Critic | **none** | anything but judge |

The two routable allowlists are **disjoint**, deliberately. Overlapping capability makes routing
arbitrary, because either agent could do the work.

**The critic has no tools** because a critic that can fetch new sources is doing research, and its
verdict becomes unfalsifiable — it can always find something to justify what it already concluded.

### The bound that keeps two models from arguing forever

Critic and producer can disagree indefinitely. `max_revisions` caps it in code — the same
principle as V0.4's tool-loop cap: *the bound is the guarantee, the prompt is a request.*

When the budget runs out with objections outstanding, the answer is returned **with the objections
attached and confidence downgraded**, not discarded and not silently presented as accepted. Three
options; the other two are "throw away work that is probably partly right" and "tell the user a
lie they cannot detect."

And a **broken critic accepts**. It is a quality gate, not a correctness requirement: if the
reviewer breaks, blocking a correct answer is worse than passing an unreviewed one.

### The measurement that caught my mistake

Routing scored **90% (9/10)** on the first run. The miss:

```
"Check whether previous runs solved a similar goal"
  expected analyst, routed to researcher
```

**The router was right. I was wrong.** I had assigned `recall_past_runs` to the analyst, reasoning
that past outcomes inform judgement. But recalling a past run is a *lookup* — structurally
identical to searching documents or recalling a fact. The tool was on the wrong agent.

Moved it; routing went to **100% (10/10)**, and the two allowlists became fully disjoint, which is
a better design independent of the score.

**This is the same shape as V0.5's recall problem and deserves the same scrutiny.** The
distinction I would defend: in V0.5 I changed the *ground truth* to match the output; here I
changed the *system* because the disagreement revealed a real design error, and the label followed
the code. The caveat stands regardless — ten self-authored cases cannot tell a good router from a
set of easy questions.

### What five milestones of a stable interface bought

`AgentTeam` satisfies `run(goal) -> AgentResult` — the same signature every agent has had since
V0.1. So adding an entire agent layer touched the executor, the orchestrator and `RunService` not
at all.

That interface was defined in V0.1 for a reason that had nothing to do with multi-agent: tests
needed a fake provider. It has now absorbed tools, orchestration, memory and specialisation
without changing.

### Two bugs worth keeping

**A "deterministic" test fake that was randomised per process.** `FakeEmbeddings` used Python's
built-in `hash()`, which is seeded per process, so retrieval tests passed or failed by luck. The
flake had been latent since V0.5.

The instructive part: `test_fake_embeddings_are_deterministic` **existed and passed the whole
time**, because it compared two calls *inside one process* where `hash()` is perfectly stable. It
measured the wrong scope and certified precisely the property it was missing. **Testing
determinism requires a value fixed outside the process** — the replacement shells out to a second
interpreter.

**The version was a literal in three files and drifted.** The first fix —
`importlib.metadata.version()` — was a plausible-looking inversion that reintroduced staleness
through a longer path, because metadata describes the last *build*, not the source. The real fix
is one definition in `amos.__version__` with the build reading from it.

*When a fact appears in three places, the fix is one definition — not better discipline about
updating three.*

---

## Chapter 9 — V0.8: asynchronous execution, and admitting what you cannot guarantee

**Goal:** goals that take minutes should not hold an HTTP connection, and a worker crash should not
lose work.

### The whole mechanism, in one statement

```sql
UPDATE runs SET status='RUNNING', claimed_at=now(), claimed_by=:worker,
                attempt_count = attempt_count + 1
 WHERE id = (SELECT id FROM runs WHERE status='QUEUED'
              ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, goal_text, attempt_count
```

`FOR UPDATE` locks the row. **Without `SKIP LOCKED`, a second worker blocks** on the row the first
holds, so N workers serialise into one — and it still looks like it works, because jobs get
processed, just never concurrently. `SKIP LOCKED` steps over locked rows.

The claim and the state change are **one statement in one transaction**, which is what makes a dead
worker recoverable without any recovery code: the database releases the lock when the connection
dies, and the row is never in a half-claimed limbo.

No broker. No Celery. The database AMOS already had.

### The demo worth remembering

Worker A claimed a run and was killed with `SIGKILL` — no cleanup, no chance to update anything.
The run sat in `RUNNING`, `claimed_by` naming a dead process. Worker B started, swept, reclaimed
it, and finished it. `attempt_count` went to 2, so the retry is visible in the data.

**Nothing detects that a worker died.** Only that a run has been held longer than the visibility
timeout. That is the whole recovery mechanism, and it is about ten lines of SQL.

### Saying what you cannot do

**Exactly-once delivery is not available and AMOS does not claim it.** A worker can finish a run
and die before recording that it did; the timeout then makes it claimable and it runs twice.

The correct response is idempotent work, not a stronger promise. AMOS's honest position:

| | |
|---|---|
| Every tool read-only | ✅ re-execution wastes tokens, corrupts nothing |
| `remember_fact` | ⚠️ a duplicate could store the same fact twice — **supersession makes that harmless by luck, not design** |
| Task-level idempotency keys | ❌ not implemented |

That middle row is written down as a gap. It is exactly the kind of thing that is comfortable to
leave unmentioned, because nothing is currently broken by it.

The visibility timeout has its own admitted failure: a worker that is merely *slow* can have its
run stolen and executed twice. The default is 600s because the cost of waiting is latency and the
cost of being aggressive is duplicate work.

### The speculative column that guessed wrong

V0.4 added `tasks.claimed_at` and a partial claimable index, with the comment *"present now because
adding a column later to a table with rows is a migration."*

The reasoning was sound. The guess was wrong: claiming happens at the **run** level, because a run
is what a client submits and polls, and a run's internal concurrency is already handled inside one
worker. The column never anticipated the right granularity.

V0.8 **removed both** rather than carry schema documenting an abandoned plan. The cost of the
speculation was never the column itself — it was that a column in a schema looks like a decision
somebody made for a reason.

### And a silent divergence that had been running for two milestones

While adding the new columns, a quick diff of the ORM models against the live schema found **five
columns in the database and absent from the models**. A V0.6 `str.replace` patch had silently
failed — the third distinct bug of that exact shape.

Nothing had broken, because the one code path using those columns (`memory/episodic.py`) goes
through **raw SQL**. The drift was invisible precisely because the model was never consulted.

Migrations and models are two descriptions of one schema, and nothing was comparing them.
`test_schema_drift.py` now does, in both directions, with `vector` columns exempted **by name**
rather than by a blanket ignore — an exemption list that names things keeps its teeth.

---

## Chapter 10 — V0.9: observability, and the seam paying off

**Goal:** emit standard traces and metrics.

This was the smallest milestone in the project, and the reason is the interesting part.

### The hard part was done in V0.1

Every log line has carried a request id since the first milestone. Every run has been persisted
and queryable since V0.3. **Correlation was already solved.** V0.9 adds a standard wire format on
top of work done for entirely different reasons — the request id existed because debugging needed
it, not because tracing was planned.

That is what a seam is worth. Nine milestones later, the thing it enables costs an afternoon
instead of a refactor.

### Two rules, and why they are tests rather than documentation

**User content stays out of spans by default.** A goal can contain a name, a pasted document, a
credential. Spans are shipped, stored and searchable — putting user input in them by default is a
data-handling decision disguised as a debugging convenience.

The *direction of the default* is the whole decision. An opt-out would ship content from every
deployment that forgot to configure it, and defaults are what systems actually run with.

Verified in the exported data, not just asserted:
```
run.execute   amos.goal.length: Int(19)   amos.request_id: 0013d45719d8413d
```
Length, not text.

**Unbounded values never become metric labels.** A run id as a metric label means one time series
per run — the standard way to take a monitoring backend down. The same value as a span attribute
is fine, because a span is one event rather than a dimension.

`ALLOWED_LABELS` is a closed set, and it is an allowlist rather than a blocklist for the same
reason `http_get` uses one: a blocklist must anticipate every unbounded field and fails open when
it misses one. That is now the third place in this codebase where the same reasoning applied.

### The bug that came from my own test

A telemetry test called `set_request_id("abc123def456")`, and the request id is a module-level
`ContextVar` — so it leaked into every test that ran afterwards. An unrelated agent test asserting
a 16-character generated id started failing, in a file that had not changed.

Fixed with an autouse fixture resetting the contextvar per test. **Autouse, because remembering to
clean up global state per test is exactly the discipline that fails silently.**

Also worth knowing: `trace.set_tracer_provider()` can only be called **once per process**. Later
calls are ignored *with a warning, not an error*, so a per-test provider works for the first test
and silently records nothing thereafter — presenting as "no spans captured" rather than "your
fixture is wrong".

---

## Chapter 11 — V1.0: evaluation, and the metric that lied twice

**Goal:** make every quality claim in the repo checkable by a command.

### Two kinds of evidence, kept apart

Most questions about an agent run can be settled by **code**: did it complete, is the output valid,
did the expected tool run, is the required fact present, was the right source cited, did it refuse
when the corpus had nothing. Facts in the trace, not opinions — reproducible, free, and unable to
be talked into agreeing.

One cannot: **is the answer supported by what was retrieved?** String overlap does not answer it. A
correct paraphrase shares few words; a fabrication can share many.

So there is exactly one LLM-judged metric, and it is reported separately and never averaged into a
headline score — because the judge is the same model family as the system it judges. Only the
deterministic checks gate CI.

### The results

```
cases          6/6 (100%)      retrieval  recall@5 100%, MRR 0.958
tool selection 100%            routing    10/10
refusal        1/1             tests      480
groundedness   1.00  (judged — weaker evidence)
```

And immediately after, the caveat that travels with them everywhere: six goals, twelve retrieval
questions and ten routing cases, **all written by the person who built the system**. Enough to
catch a regression. Nowhere near enough to characterise quality.

### It lied twice, and both times it was the metric

**A rate limit is not a quality failure.** The first run scored 5/6, the failure being
`ProviderRateLimitError`. That is an infrastructure limit — scoring it as a failure makes the suite
say *"the system answered badly"* when it means *"we could not measure"*. Cases are now
*unmeasurable*: excluded from the rates, reported separately, and not failing the gate. Otherwise
part of the score is a measurement of the free tier.

(It also surfaced a fourth quota shape: `flash-lite` is **15 per minute** where `flash` is 20 per
day.)

**The second one nearly became a false finding.** The refusal case scored 0/1 — "should have
refused but produced a confident answer". The most important case in the suite, apparently failing
in the worst way possible: inventing a Kubernetes policy that does not exist.

Reading the actual output showed the system had refused correctly:

> *"Therefore, AMOS does not have a Kubernetes autoscaling policy or configured replica counts."*

The model had phrased it in wording my keyword detector did not cover.

**A crude metric does not merely under-measure. It manufactures false failures that are
indistinguishable from real ones until you go and read the output.** I was one paragraph away from
writing up a brittle detector as a quality finding about the system.

The fix has two halves, and the second matters as much as the first: broaden the markers, *and*
add a test asserting confident inventions are still caught — because broadening a detector until
everything looks like a refusal would pass the exact failure it exists to catch.

### CI, finally

Technical debt since V0.3. It runs the suite **with and without a database** (because "most tests
need no database" is a claim, and an untested claim is a wish), applies migrations in **both
directions** (V0.4 shipped an irreversible one, caught by hand), and runs with **no API key** —
which turns "tests never touch the network" from documentation into something enforced.

It deliberately does *not* run the evaluation suite. That costs quota, and a gate that fails for
reasons unrelated to the change is worse than no gate.
