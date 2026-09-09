# Interview — Memory (V0.6)

**Advance gate: V0.7 does not begin until these can be answered unaided.**

---

### Why isn't everything stored as vectors? It's an AI system.

Three reasons, each of which the schema encodes:

**Exact recall is a key lookup.** "What is the user's name?" must return *the* name. Similarity
search returns the most similar-looking fact, which in a store with several names is sometimes the
wrong person's. No similarity threshold fixes that — the failure is semantic, not numeric.

**Contradictions need ordering.** When a fact changes, the old value must stop being current. A
vector index has no notion of superseded: it returns both, ranked by distance, with nothing to say
which is true.

**Provenance is a join**, not a nearest neighbour.

So `subject` is a normalised key for exact lookup and contradiction detection, and the embedding is
a *secondary* index for questions that arrive without a key. `recall_facts` tries exact first,
always.

### How are contradicting facts resolved?

Newest wins, and the previous row is marked `superseded_by` the new one. **A rule, not a
judgement — and specifically not something the model decides.** "Current" is then a deterministic
query: `WHERE superseded_by IS NULL`.

Superseded rows are kept rather than deleted, which buys three things: a changed fact stays
auditable, a bad write stays recoverable, and the history of a subject is inspectable.

### Why normalise the subject?

`"User's Name"`, `"user name"` and `"USER_NAME"` must collide. Without normalisation the same fact
stored with different capitalisation produces two "current" rows for one subject, and contradiction
resolution silently stops working — the store now contains two conflicting truths and reports both
as current.

### Why is there no `episodes` table?

Because an episode **is** a run. It has a goal, an outcome, a token cost and a duration, all of
which `runs` already stores. A separate table would duplicate all of that to add an embedding and
a lesson.

Episodic memory is two columns on `runs` plus queries — **an index on an existing store, not a new
store**. Same instinct that kept AMOS on one database: before adding a place to put things, check
whether the thing already has one.

### What is the difference between episodic memory and the V0.3 execution trace?

They are the same rows, asked different questions.

The **trace** answers "what exactly happened on run X?" — you have the id, you want the detail.
**Episodic memory** answers "have I done anything like this before?" — you have a goal, you want
similar past runs and how they went.

That is why episodic memory needed only an embedding and a lesson: the facts were already stored,
they just were not *findable by similarity*.

### Why must the current run be excluded from episodic recall?

V0.3 writes the run row **before** executing it, so by the time a goal asks "have I done this
before?", it is already in the table — and is trivially the most similar past goal, with cosine
similarity 1.0. Without `exclude_run_id`, episodic recall returns the question as its own answer.

A nice example of an earlier design decision (record-before-execute, for crash evidence) creating
an obligation in a later one.

### Why is the lesson derived rather than written by the model?

An extra LLM call per run to summarise what happened would cost roughly 5% of the daily quota to
restate what the data already says: how many tasks succeeded, which failed and why, which tools
ran. The facts are already recorded; generating prose about them is not worth an API call.

### Why can't a failure to record an episode fail the run?

Because by that point the answer is already computed and already persisted. Episodic recording is
an *index update* — losing it degrades future recall slightly. Failing the request over it would
throw away correct, paid-for work to protect a convenience.

So it runs after the outcome is committed, wrapped, and logged on failure.

### Working memory exists but is not persisted. Why?

Working memory is scratch — intermediate values that only mean anything inside one execution. It
is already built: `Executor._build_goal` passes a task's result to its dependents, and that context
dies with the run.

Persisting it would fill the database with data nothing reads, and blur the line between "what
happened" (the trace) and "what is true" (semantic memory).

### Why is conversation memory not built at all?

Because AMOS has no multi-turn conversation. `POST /v1/goals` is a single request/response with no
session identifier — there is no conversation to remember. Building a store for it now would mean
inventing a shape for something that does not exist, which is the trap ADR-007 exists to avoid.

Saying "not built, and here is why" is more useful than a speculative schema.

### `remember_fact` writes. Isn't that the write tool the registry refuses?

It is the first thing AMOS does that **persists a decision the model made**, and that deserves the
discomfort. It is deliberately kept at `READ_LOCAL` — a bounded, single-row, reversible write —
rather than being given `WRITE`, which the registry refuses until an approval workflow exists.

What protects it: bounded lengths, deterministic supersession (a bad fact *overwrites* rather than
sitting alongside the truth), retained history so a bad write is recoverable, and `source_run_id`
provenance.

**What does not protect it:** there is no approval step and no check on where the content came
from. A malicious page fetched by `http_get` could talk the model into remembering something false,
and the system would repeat it in later sessions. That is a real limitation, not a theoretical one.

### The memory tools existed and were tested, but the agent never used them. What happened?

A patch to `build_registry` silently failed to apply — the text it matched against had been
reformatted — so the tools were constructed, tested and documented but **never registered**. The
agent was offered four tools instead of seven and answered "I have noted that" without storing
anything.

Every unit test passed, because **nothing asserted what the registry contains**. The fix was
`tests/unit/test_tool_wiring.py`: the wiring is a behaviour, and behaviours need tests.

The wider lesson: unit tests verify components; nothing was verifying that the components were
*connected*. That gap is invisible until an end-to-end demo, which is why running the demo is in
the Definition of Done.

---

## What V0.6 does NOT demonstrate

- **No forgetting** — memories accumulate forever; no TTL, decay or eviction
- **No confidence decay** — the column exists and is always 1.0
- **No cross-subject conflict detection** — two facts under different keys may contradict freely
- **No user scoping** — single user, so no `user_id` column
- **No automatic extraction** — nothing scans a finished run for things worth remembering
- Still one agent type. **Not multi-agent** (V0.7)
