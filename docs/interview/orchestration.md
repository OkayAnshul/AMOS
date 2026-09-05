# Interview — Orchestration (V0.4)

**Advance gate: V0.5 does not begin until these can be answered unaided.**

---

### Why can the LLM not move a task between states?

Because then none of the system's guarantees would hold. If a model could mark its own task
`SUCCEEDED`, "this task completed" would mean "the model said so" — and a model that
misunderstood, hallucinated, or was steered by injected content could declare success on work it
never did.

Concretely: only `orchestration/state.py` changes a task's state, through `assert_transition`,
which raises on anything the table forbids. Nothing in the agent or provider layers can reach it.

This is the invariant from `docs/02-system-architecture.md` made executable: **LLMs handle
uncertainty, software handles guarantees.**

### Why do illegal transitions raise instead of logging a warning?

Because a silently accepted illegal transition means the system's recorded state no longer
describes reality, and every decision built on it afterwards is wrong — including the trace you
would use to debug it. A loud failure at the point of the bug beats a plausible, incorrect
history.

53 illegal transitions are asserted to raise. A separate test asserts the module's table and the
test's expectations agree, so they cannot drift apart.

### Why does a retry return the task to `READY` rather than a dedicated retry state?

So there is exactly one code path for "about to run". A retried attempt then cannot behave
differently from a first attempt, because it is not a different case — no separate branch to
forget to update when the execution path changes.

`FAILED` and `TIMED_OUT` are still kept distinct from `PERMANENTLY_FAILED`: the transient states
are where the retry decision happens, the permanent one is terminal. Collapsing them would make
"was this retried, and how often?" unanswerable from the data.

### Why is jitter necessary? What breaks without it?

Five tasks fail at the same instant (a rate limit, a brief outage). Without jitter all five wait
exactly 0.5s, retry simultaneously, fail simultaneously, wait exactly 1.0s, and collide again —
forever, in lockstep. The backoff spaces attempts out in *time* but does nothing to spread them
across *tasks*, so it recreates the burst that caused the failure.

Full jitter picks uniformly from `[0, delay]`, decorrelating them.

There is a test for this beyond the bounds check — `test_jitter_actually_varies`. Without it,
"jitter" could be implemented as a constant and still pass every other test while doing nothing.

### What happens when one of five tasks fails?

Its dependents are `SKIPPED`, transitively — a dependent of a dependent is skipped too, or it
would wait forever on something that will never move. Unrelated branches run to completion.

The run reports `PARTIALLY_COMPLETED`, and the successful work is kept.

### Why is `PARTIALLY_COMPLETED` a real outcome rather than just failure?

Because a research goal where three sources answered and one timed out produced genuine value.
Forcing that into binary success/failure means either discarding good work or reporting a
success that wasn't. The run's stored status carries it, so a partial run never reads as an
unqualified success in the trace.

### How are cycles detected, and why does it matter *when*?

Iterative DFS with an explicit stack (not recursion — a confused plan should be rejected, not
crash the process), returning the actual cycle so the planner's repair prompt can name it.

The *when* is the important half: validation happens **before anything is persisted**. A cyclic
plan that reached the database would be a run that can never complete, holding rows that look
live forever. Validate, then write.

### The planner is an LLM. What stops a bad plan from damaging the system?

Layers, all before persistence: Pydantic checks shape; the validator checks unique ids,
resolvable dependencies, no self-dependency, acyclicity, and a 10-task cap. A rejected plan is
re-prompted with the specific error, bounded by attempts, then `PlanningError`.

The task cap is a blast-radius bound as much as a sanity check — 50 tasks is a runaway, and each
task costs real API calls against a 20/day quota.

### Why is synthesis skipped when only one task ran?

Because that task's answer *is* the answer, and paying an API call to rephrase it is waste. On a
20-request/day free tier that is a measurable fraction of the day's budget, not a micro-optimisation.

Synthesis is also skipped when nothing succeeded — there is nothing to combine, and the failure
response is assembled from the recorded errors.

### How is the executor guaranteed to terminate?

Each loop iteration either advances at least one task toward a terminal state or finds nothing
runnable and stops. Skipping is applied to a fixed point before each pass, so no task is left
waiting on something that will never move. An assertion at the end confirms every task ended in
a terminal state — if that ever fires, the loop had a hole.

### Why store `depends_on` as a Postgres `UUID[]` rather than a join table?

A `task_dependencies` table is the textbook normalisation, but every read of this graph loads a
run's tasks together anyway, so the join buys nothing and costs a table plus a join on the
hottest path. Postgres arrays are first-class and indexable.

Note the stored array holds **row UUIDs**, not the planner's symbolic refs (`t1`), so the graph
remains intact without the original plan text.

### What did the Alembic naming convention fix?

Autogenerate produced an unnamed foreign key, and the generated downgrade then failed with
`Can't emit DROP CONSTRAINT ... it has no name`. The migration applied fine and **could not be
reversed** — discovered only when reversing it.

A `naming_convention` on the metadata gives every constraint a deterministic name, so it is
droppable by name from any migration. An irreversible migration is a one-way door you find out
about at the worst moment.

---

## What V0.4 does NOT demonstrate

- **No re-planning.** A failed task is retried as written; the planner is not asked to try a
  different approach
- **No worker, no queue.** Tasks within a run run concurrently, but execution still happens
  inside the HTTP request. `SKIP LOCKED` is V0.8
- **No resumption.** A crash leaves an abandoned `RECEIVED` run that nothing sweeps up
- **Tasks are not idempotent.** Safe only because every tool is read-only — this becomes a real
  bug the moment a write tool exists, which is why the registry refuses to register one
- **Still one agent type.** Not multi-agent; that is V0.7 and is not claimed
- No retrieval, no memory. pgvector is installed and still unused
