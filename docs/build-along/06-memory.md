# V0.6 — Memory

**+1,191 lines.** The code is the easy part. The decision is **which store each kind of memory
belongs in** — and the reflexive answer is wrong.

---

## Where you are

An agent that plans, uses tools, retrieves with citations, and records everything. It knows nothing
about *you* between requests.

## The problem

Tell it your preferred language today and it has no idea tomorrow. Every session starts cold.

## The trap, stated first

**"Embed everything and search by similarity" is the reflexive design and it is wrong for facts.**

| | Why similarity fails |
|---|---|
| *"What is the user's name?"* | returns the **most similar** fact. In a store with several names, sometimes the wrong person's. No threshold fixes this — the failure is semantic, not numeric |
| A fact that changed | a vector index has no notion of **superseded**. It returns old and new, ranked by distance, with nothing saying which is true |
| *"Where did this come from?"* | provenance is a foreign key, not a nearest neighbour |

So: **relational first**, with the embedding as a *secondary* index for questions arriving without
a key.

---

## The decision

The five memory kinds were named back in the domain model. This milestone decides where each lives:

| Kind | Store | Status |
|---|---|---|
| **Working** | in-process, run-scoped | already built at V0.4, without a name |
| **Conversation** | — | **deliberately not built** |
| **Semantic** | `memories` table + secondary vector index | build it |
| **Episodic** | two columns on `runs` | build it |
| **Knowledge** | `documents` / `chunks` | V0.5 |

**Working memory is already done.** `Executor._build_goal` passes a task's result to its dependents,
and that context dies with the run. **Do not persist it** — it is scratch, and storing it fills the
database with data nothing reads while blurring "what happened" (the trace) against "what is true"
(semantic memory).

**Conversation memory is not built, and saying so is the right answer.** There is no multi-turn API
— `POST /v1/goals` is a single request/response with no session id. Building a store for a
conversation that does not exist would be inventing a shape for nothing.

---

## Build order

### 1. Migration: `memories`, plus two columns on `runs`

```sql
memories (
    id, subject, content, confidence,
    source_run_id,           -- provenance: which run learned this
    superseded_by,           -- a CHAIN, not a delete
    embedding vector(1536),  -- secondary index
    created_at
)
CREATE INDEX idx_memories_current ON memories (subject) WHERE superseded_by IS NULL;
```

**Partial index**, because almost every query wants only current facts and superseded rows
accumulate without ever being read on the hot path.

### 🔎 Episodic memory gets no table

An episode **is** a run. Goal, outcome, tokens, duration — `runs` already stores all of it. A
separate `episodes` table would duplicate every one of those to add an embedding and a lesson.

```sql
ALTER TABLE runs ADD COLUMN lesson TEXT;
ALTER TABLE runs ADD COLUMN goal_embedding vector(1536);
```

**Episodic memory is an index on an existing store, not a new store.** Before adding somewhere to
put things, check whether the thing already has a home. Same instinct that kept this on one
database.

---

### 2. `src/amos/memory/semantic.py` (~180 lines)

```python
def normalise_subject(subject: str) -> str:
    """'User's Name', 'user name', 'USER_NAME' must all collide."""

class SemanticMemory:
    async def remember(self, subject, content, *, confidence=1.0, source_run_id=None) -> Fact: ...
    async def recall_exact(self, subject) -> Fact | None:
        """Deterministic. No ranking involved."""
    async def recall_similar(self, query, *, limit=5, min_score=0.3) -> list[Fact]:
        """The SECONDARY path. Never a substitute for recall_exact when a key is known."""
    async def history(self, subject) -> list[Fact]:
        """Every value this subject has held. Possible only because supersession is a chain."""
```

**Why normalise the subject** — without it, the same fact stored with different capitalisation
produces **two "current" values** for one subject, and contradiction resolution silently stops
working. The store now contains two conflicting truths and reports both.

**Contradiction resolution is newest-wins, decided in code.** A rule, not a judgement, and
specifically **not** something the model decides. "Current" is then `WHERE superseded_by IS NULL`.

**Superseded rows are kept, not deleted.** Three things that buys: a changed fact stays auditable, a
bad write stays recoverable, and the history of a subject is inspectable.

⚠️ **Getting the write order wrong is easy and Postgres will tell you immediately.**

<details><summary>Cause, and the comment that made it worse</summary>

The obvious order is: mark the old row superseded, then insert the new one — so there is never a
moment with two current facts.

**It cannot work.** `superseded_by` is a foreign key to `memories.id` and the new row does not exist
yet. `ForeignKeyViolationError`.

Insert first, then update — and exclude the new row (`AND id <> :new_id`), or it supersedes itself
and the subject ends up with **zero** current facts.

The original carried a comment explaining why supersede-first was necessary. It was wrong twice: the
ordering was impossible, *and* the danger it warned about was not real, because both statements
share a transaction so no other transaction sees the intermediate state.

**A confident comment justifying an impossible design is worse than no comment** — it discourages
the next reader from checking.
</details>

**Test first** (these need a real database — the behaviour under test **is** the SQL):

| Test | Why |
|---|---|
| exactly **one** current fact per subject after repeated writes | the invariant the partial index depends on |
| superseded facts kept and inspectable | audit and recovery |
| **similarity never returns superseded facts** | the reason facts are not vectors alone |
| subject normalisation across capitalisation | two-current-values |
| different subjects do not interfere | scoping |

---

### 3. `src/amos/memory/episodic.py` (~110 lines)

```python
class EpisodicMemory:
    async def record(self, run_id, goal, lesson=None) -> None:
        """AFTER the outcome — the lesson is only knowable once the run finished."""
    async def recall_similar(self, goal, *, limit=3, min_score=0.5,
                             exclude_run_id=None) -> list[Episode]: ...
```

**`exclude_run_id` is not optional, and the reason is a consequence of V0.3.** You write the run row
*before* executing, so by the time a goal asks "have I done this before?", **it is already in the
table** — and is trivially the most similar past goal at similarity 1.0. Without excluding it,
episodic recall returns the question as its own answer.

A nice example of an earlier design decision creating an obligation in a later one.

**Derive the lesson, do not generate it.** Assemble it from recorded facts — how many tasks
succeeded, which failed, which tools ran. An extra LLM call per run to restate what the data
already says costs ~5% of a daily quota.

**Recording an episode must never fail a run.** It happens after the outcome is committed, wrapped
and logged. The answer is already correct and already saved; losing it to protect an index entry is
strictly worse.

---

### 4. `src/amos/memory/tools.py` (~180 lines)

`remember_fact`, `recall_facts`, `recall_past_runs`.

**`recall_facts` tries exact before similar.** If the caller knows the key, a ranking is the wrong
answer.

**The honest discomfort:** `remember_fact` is the first thing this system does that **persists a
decision the model made**. Every earlier tool was read-only. A fact written because of a
prompt-injected instruction is a persistent lie repeated in later sessions.

What protects it: bounded lengths, deterministic supersession (a bad fact *overwrites* rather than
sitting alongside the truth), retained history, and provenance.

**What does not:** there is no approval step and no check on where the content came from. Keep it at
`READ_LOCAL` rather than `WRITE` — the registry refuses `WRITE` until an approval workflow exists,
and that constraint is doing real work.

---

### 5. ⚠️ Wire it up — and then **check that you did**

This is where the original lost a day, and the lesson generalises further than memory.

**Symptom:** you ask it to remember something. It replies *"I have noted that."* The `memories`
table is empty.

<details><summary>Two causes, and the second one only appears later</summary>

**First:** a patch to `build_registry` silently failed to apply, so the tools were written, tested
and documented — and never registered. 344 tests passed with the feature completely unreachable.

**Second, found after V1.0:** even once registered globally, `remember_fact` was in **no agent's
allowlist** (V0.7). With routing on, every specialist's registry filtered it out and storing was
*structurally impossible*. Measured: **0% store rate, 38% false claims.**

Both were invisible because **nothing asserted what tools the agent actually receives.**

Write these now:

```python
def test_all_tools_are_registered_when_a_database_is_present(): ...
def test_every_registered_tool_is_reachable_by_at_least_one_agent():
    """Registration and reachability are DIFFERENT PROPERTIES.
       A tool nobody can call is a tool that does not exist."""
```

And the wider lesson, which cost three prompt-level fixes before anyone checked the registry:
**when a symptom fits "the model chose badly", find the deterministic explanation first.** That
story is unfalsifiable enough to be comfortable.
</details>

---

## Checkpoint

**The only test that means anything is across processes** — one process answering from a variable
would look identical from outside.

```bash
.venv/bin/python -m amos &
curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Remember for future sessions: my preferred language is Python."}' \
  | jq '.tool_outcomes[].name'          # must include remember_fact

kill %1                                  # KILL THE PROCESS

.venv/bin/python -m amos &                # a NEW one
curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"What is my preferred language?"}' | jq '.response.answer'
```

And check the table directly:
```sql
select subject, source_run_id, content from memories where superseded_by is null;
```
`source_run_id` must not be NULL. If it is, provenance is not wired — and provenance is one of the
three reasons memory is relational at all.

---

## What this unlocks

Nothing structural — this is a capability milestone. But `recall_past_runs` gives V0.7's researcher
something to reach for, and the allowlist bug above is waiting for you there.

Next: [`07-multi-agent.md`](07-multi-agent.md) — where "multi-agent" becomes an honest word, and
where you must not forget `remember_fact`.
