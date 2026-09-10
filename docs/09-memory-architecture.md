# 09 — Memory Architecture

**Written at V0.6**, once there was enough experience to know what AMOS actually needs to
remember. The five memory kinds were named in `04-domain-model.md` at Phase 0; **this document's
job is deciding which store each belongs in**, which was the real work of the milestone.

## The decision

| Kind | Store | Status |
|---|---|---|
| **Working** | in-process, run-scoped | built (implicitly, V0.4) |
| **Conversation** | — | **deliberately not built** |
| **Semantic** | `memories` table + secondary vector index | built |
| **Episodic** | two columns on `runs` | built |
| **Knowledge** | `documents` / `chunks` | built (V0.5) |

## Why not put everything in vectors?

This is the question the milestone exists to answer, because "embed it and search by similarity"
is the reflexive design and it is wrong for facts.

**1. Exact recall is a key lookup.** "What is the user's name?" must return *the* name. Similarity
search returns the most similar-looking fact — and in a store containing several names, that is
sometimes the wrong person's. There is no threshold that fixes this, because the failure is
semantic, not numeric.

**2. Contradictions need ordering.** When a fact changes, the old value must stop being current.
A vector index has no notion of *superseded*: it will happily return both the old and new value,
ranked by cosine distance, with nothing to say which is true. `test_similarity_never_returns_superseded_facts`
is the assertion that this is handled.

**3. Provenance is a join.** "Where did this come from?" is a foreign key to a run, not a nearest
neighbour.

So the design is **relational first**: `subject` is a normalised key used for exact lookup and
contradiction detection, and the embedding is a *secondary* index for questions that arrive
without a key.

## Semantic memory

```sql
memories (
    id, subject, content, confidence,
    source_run_id,          -- provenance: which run learned this
    superseded_by,          -- a chain, not a delete
    embedding vector(1536), -- secondary index
    created_at
)
CREATE INDEX idx_memories_current ON memories (subject) WHERE superseded_by IS NULL;
```

### Subject normalisation

`"User's Name"`, `"user name"` and `"USER_NAME"` all normalise to `user_s_name` / `user_name`.
Without this, the same fact stored with different capitalisation produces two "current" values for
one subject and contradiction resolution silently stops working.

### Contradiction resolution is deterministic

A new fact for an existing subject supersedes the old one. **Newest wins — a rule, not a
judgement, and specifically not something the model decides.**

Superseded rows are **kept**, which buys three things: a changed fact stays auditable, a bad write
stays recoverable, and `memory_history()` can show every value a subject has held.

### The ordering bug worth knowing about

The first implementation superseded the old row *then* inserted the new one, with a comment
explaining that the reverse order would briefly leave two current facts. Postgres rejected it
immediately: `superseded_by` is a foreign key to `memories.id`, and the new row did not exist yet.

The comment was wrong twice over. Insert-then-update is also **safe**, because both statements run
in one transaction and no other transaction ever observes the intermediate state. A confident
comment justifying an impossible ordering is worse than no comment.

The update must also exclude the new row (`AND id <> :new_id`), or it supersedes itself and the
subject ends up with *zero* current facts.

## Episodic memory — and why it has no table

An episode **is** a run. It has a goal, an outcome, a token cost, a duration — all of which `runs`
already stores. A separate `episodes` table would duplicate every one of those to add an embedding
and a lesson.

So episodic memory is `runs.goal_embedding` + `runs.lesson`, plus queries.
**It is an index on an existing store, not a new store.**

This is the same instinct that kept AMOS on one database (ADR-001): before adding somewhere to put
things, check whether the thing already has a home.

### Two details that matter

**The current run must be excluded.** V0.3 writes the run row *before* executing, so by the time a
goal asks "have I done this before?", it is already in the table — and is trivially the most
similar past goal. Without `exclude_run_id`, episodic recall returns the question as its own
answer.

**The lesson is derived, not generated.** It is assembled from recorded facts — how many tasks
succeeded, which failed and why, which tools ran. Asking the model to write a sentence summarising
the run would cost an extra API call per run, roughly 5% of the daily quota, to restate what the
data already says.

**Recording an episode cannot fail a run.** It happens after the outcome is persisted, wrapped and
logged. The answer is already correct and already saved; losing the index entry is not worth
losing the run over.

## Working memory

Already built, without a name for it. The executor passes a task's result to its dependents
(`Executor._build_goal`), and that context dies with the run.

**It is deliberately not persisted.** Working memory is scratch — intermediate values that only
mean anything inside one execution. Storing it would fill the database with data nothing ever
reads, and blur the line between "what happened" (the trace, V0.3) and "what is true" (semantic
memory).

## Conversation memory — deliberately not built

AMOS has no multi-turn conversation API. `POST /v1/goals` is a single request/response with no
session identifier, so there is no conversation to remember.

Building a conversation store now would mean inventing a shape for something that does not exist —
exactly the trap ADR-007 was written to avoid. When a conversational interface arrives, this
section gets written; until then, saying "not built" is more useful than a speculative schema.

## Memory as tools

All three are tools (`remember_fact`, `recall_facts`, `recall_past_runs`), so the agent decides
when memory is relevant and they inherit argument validation, timeouts and trace visibility.

`recall_facts` tries **exact before similar**. If the caller knows the key, a ranking is the wrong
answer.

### The honest limitation

`remember_fact` is the first thing AMOS does that **persists a decision the model made** — every
earlier tool was read-only. A fact written because of a prompt-injected instruction is a
persistent lie the system will repeat in later sessions.

What is in place: bounded lengths, deterministic supersession (a bad fact overwrites rather than
accumulating alongside the truth), retained history so a bad write is recoverable, and provenance
via `source_run_id`.

**What is not in place: any approval step, and no check on the content's origin.** A malicious page
fetched by `http_get` could still talk the model into remembering something false. The tool stays
at `READ_LOCAL` permission rather than `WRITE` precisely because the registry refuses `WRITE` until
the approval workflow in `13-security.md` exists.

## Reliability — measured

Storage was **structurally impossible** with multi-agent enabled until 2026-09-10:
`remember_fact` was in no `AgentSpec` allowlist, so every specialist's registry filtered it out.

| Configuration | store rate | false-claim rate |
|---|---|---|
| Allowlist broken (the original bug) | **0%** (0/8) | **38%** |
| Allowlist fixed | **100%** (8/8) | **0%** |

Reproduce: `make memory-trials`. Ground truth is read from the `memories` table, not from the tool
outcome — the outcome says a call was made, the row says the write landed.

### The honesty guarantee

`MemoryReconciler` (`src/amos/memory/reconcile.py`) compares what an answer *claims* against what
was *written*, and when they disagree it allows one bounded retry through the real tool, then
appends an explicit `NOT STORED` caveat and downgrades confidence.

| | |
|---|---|
| **Guaranteed** | The system never claims to have remembered something it did not store |
| **Best effort** | The fact usually does get stored |
| **Not promised** | That every stated fact is always stored — that needs extraction, which is another model-decided write |

**It has never been observed to fire since the allowlist was fixed.** It is kept because the
failure was real and measured at 38%, its cost is zero on the common paths, and the guarantee does
not depend on the allowlist staying correct. If it still has not fired by a future milestone,
delete it — the same rule that removed `_finalise` at V0.7.

## Not done

- **No forgetting.** Memories accumulate forever; there is no TTL, decay or eviction.
- **No confidence decay.** The `confidence` column exists and is always 1.0.
- **No conflict detection across subjects** — only within one subject. Two facts under different
  keys can contradict each other freely.
- **No user scoping.** Single user, so `memories` has no `user_id`. Adding one now would mean a
  column with one value everywhere (the same reasoning as the absent `users` table, `05-data-model.md`).
- **No automatic extraction.** Facts are stored when the model calls the tool; nothing scans a
  completed run for things worth remembering.
