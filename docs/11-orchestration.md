# 11 — Orchestration

**Written at V0.4.** How a goal becomes a task graph and gets executed.

## The split

```
Goal ──▶ Planner (LLM)  ──▶ Plan  ──validate──▶ Task DAG
                                                    │
                                              Executor (code)
                                                    │
                                    ┌───────────────┼───────────────┐
                                    ▼               ▼               ▼
                              TaskRunner      TaskRunner      TaskRunner
                              (the V0.2 tool-using agent, one task at a time)
                                                    │
                                              Synthesis (LLM)
```

The planner and the synthesiser are the only places an LLM decides anything at this layer. The
executor contains **no LLM calls at all**. That is the concrete form of the project's central
invariant:

> **LLMs handle uncertainty. Software handles guarantees.**

If the model could mark its own task SUCCEEDED, or grant itself another retry, every property
below would be advisory rather than guaranteed.

| The model decides | Code decides |
|---|---|
| how to decompose the goal | whether a task may change state |
| what each task should say | whether a retry is permitted |
| which tool to use | when to give up |
| how to phrase the final answer | what happens to a failed task's dependents |

## The state machine

`src/amos/orchestration/state.py`. **Only this module moves a task between states**, through
`assert_transition`, which raises on anything the table does not allow.

```
   PENDING ──deps satisfied──▶ READY ──claimed──▶ RUNNING
      │                          ▲                   │
      │                          │       ┌───────────┼───────────┐
      │                          │       ▼           ▼           ▼
      │                          │  SUCCEEDED     FAILED     TIMED_OUT
      │                          │                   │           │
      │                          └── retries left ───┴───────────┘
      │                                              │  none left
      │                                              ▼
      └──dependency failed──▶ SKIPPED       PERMANENTLY_FAILED
```

Design points that are easy to get wrong:

**A retry returns the task to `READY`, not to a special retry state.** One code path for "about
to run" means a retried attempt cannot behave differently from a first one — no separate branch
to forget to update.

**`FAILED` and `TIMED_OUT` are distinct from `PERMANENTLY_FAILED`.** The transient states are
where the retry decision happens; the permanent one is terminal. Collapsing them would make
"was this retried, and how often?" unanswerable from the data.

**Illegal transitions raise rather than warn.** A silently accepted illegal transition means the
system's state no longer describes reality, and every later decision built on it is wrong.
Failing at the point of the bug beats producing a plausible, incorrect trace.

**Terminal states permit nothing.** In particular nothing can leave `SUCCEEDED` — no confused
caller can re-run completed work.

Tested exhaustively: all 11 legal transitions asserted, and **all 53 illegal ones asserted to
raise**. A separate test asserts the module's table and the test's expectations agree, so the
two cannot drift.

## Plan validation

A plan is untrusted structure from an LLM. Pydantic checks its shape; `Plan`'s validator checks
its meaning, **before a single row is written**:

- ids are unique
- every `depends_on` resolves to a task in the plan
- no task depends on itself
- the graph is acyclic
- at most 10 tasks (a bound on blast radius, and on cost)

Cycle detection is an **iterative** DFS with an explicit stack, not recursion — a confused plan
should be rejected, not crash the process. It returns the actual cycle (`t1 -> t2 -> t1`) rather
than a boolean, so the planner's repair prompt can name what was wrong.

Order matters: a cyclic plan that reached the database would be a run that can never complete,
holding rows that look live forever.

## Execution

The executor loops:

1. **Skip unreachable tasks** — anything depending on a `PERMANENTLY_FAILED` or `SKIPPED` task.
   Repeated until stable, because skipping propagates: if `t3` depends on `t2` depends on failed
   `t1`, `t3` must be skipped too, or it waits forever on something that will never move.
2. **Promote ready tasks** — `PENDING → READY` once all dependencies have `SUCCEEDED`.
3. **Run every ready task concurrently** via `asyncio.gather`. This is where a DAG earns its
   keep over a list: tasks with no dependency on each other do not wait for each other.
4. Stop when nothing is runnable.

Termination is guaranteed because each iteration either advances a task toward a terminal state
or finds nothing runnable. An assertion at the end confirms every task ended terminal.

## Retries

`src/amos/orchestration/retry.py`. Three properties, each load-bearing:

- **Bounded** — a retry budget is a cost ceiling. Unbounded retries against a 20-request/day
  quota would burn a day on one broken task.
- **Exponential** — an overloaded dependency needs room to recover; retrying immediately makes
  it worse.
- **Jittered** — the one that is easy to omit. Without jitter, tasks that fail at the same
  moment retry at the same moment, and keep colliding on every subsequent attempt: a
  self-inflicted thundering herd that *synchronises* rather than spreading out. Full jitter
  picks uniformly from `[0, delay]`.

The retry budget is **per task**, not per run — one flaky task must not consume the allowance of
its siblings.

## Run outcomes

| Outcome | Meaning |
|---|---|
| `COMPLETED` | every task succeeded |
| `PARTIALLY_COMPLETED` | some succeeded, some did not |
| `FAILED` | none succeeded |

`PARTIALLY_COMPLETED` exists deliberately. A research goal where three sources answered and one
timed out produced real value. Forcing that into binary success/failure would either discard good
work or overstate what happened. The run's stored status carries it, so a partial run never reads
as an unqualified success.

## Cost, and where it shaped the design

A three-task plan costs 1 planning call + 3×2 execution calls + 1 synthesis = **8 calls**, out of
20 per day. Two consequences:

- **Synthesis is skipped when a single task ran.** Its answer is already the answer; a call to
  restate it is pure waste.
- **Synthesis is skipped when nothing succeeded.** There is nothing to combine.
- `AMOS_PLANNING_ENABLED=false` falls back to the single-shot V0.2 agent for goals that do not
  need decomposition.

## Not yet

- **No re-planning.** A failed task is retried as written; the planner is not asked for a
  different approach. V0.4's planner runs once.
- **No concurrency across runs.** Tasks within a run run concurrently, but there is no worker
  and no queue — that is V0.8, where `claimed_at` and the partial `idx_tasks_claimable` index
  (both already in the schema) come into use.
- **No task-level timeout** distinct from the provider timeout.
- **No cost budget per run**, only a retry budget.
