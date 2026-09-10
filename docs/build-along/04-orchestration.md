# V0.4 — Planner and executor

**+1,902 lines. The largest milestone, and the one carrying the most transferable ideas.**

State machines, DAGs, exponential backoff with jitter, failure containment. These come up in
interviews about systems that have nothing to do with AI. **If you build only four milestones,
make this the fourth.**

---

## Where you are

An agent that uses tools, with every run durable and inspectable. It handles one goal as one unit
of work.

## The problem

*"Work out 17% of 2340 and 23% of 1500, then say which is larger."*

One agent loop can muddle through, but it cannot express that the first two computations are
independent and the third depends on both. So it serialises what could be parallel, and when one
part fails the whole thing fails.

More importantly: **there is nowhere for partial success to live.** Three sources answering and one
timing out is a real outcome, and today you can only report success or failure.

## What you will have at the end

A goal that becomes a task graph, executes with independent branches concurrently, retries what
fails, contains what cannot be recovered, and reports `PARTIALLY_COMPLETED` when that is the truth.

---

## The one rule this milestone exists to enforce

> **Only `orchestration/state.py` moves a task between states, and it raises on anything the
> transition table does not permit.**

This is the invariant *"LLMs handle uncertainty; software handles guarantees"* made executable.

If a model could mark its own task `SUCCEEDED`, "this task completed" would mean "the model said
so" — and a model that misunderstood, hallucinated, or was steered by injected content could
declare success on work it never did.

| The model decides | Code decides |
|---|---|
| how to decompose the goal | whether a task may change state |
| what each task should say | whether a retry is permitted |
| which tool to use | what happens to a failed task's dependents |

---

## Build order

### 1. `src/amos/orchestration/state.py` (~110 lines) — **write this first**

**Why now** — before anything can create a task, decide exactly what a task may do. Write it after
the executor and you will find yourself adding transitions to make code work, which is precisely
backwards.

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

**Write**
```python
class TaskState(StrEnum):
    PENDING; READY; RUNNING; SUCCEEDED; FAILED; TIMED_OUT; PERMANENTLY_FAILED; SKIPPED

_TRANSITIONS: dict[TaskState, frozenset[TaskState]]     # exactly the diagram
TERMINAL_STATES      = {SUCCEEDED, PERMANENTLY_FAILED, SKIPPED}
UNRECOVERABLE_STATES = {PERMANENTLY_FAILED, SKIPPED}    # a dependency in these will never succeed

class IllegalTransitionError(AmosError):
    """Names the allowed transitions from the current state."""

def can_transition(current, target) -> bool: ...
def assert_transition(current, target) -> TaskState:
    """Returns target if legal, else RAISES."""
```

**Three design points that are easy to get wrong:**

**A retry returns the task to `READY`, not a retry-specific state.** One code path for "about to
run" means a retried attempt cannot diverge from a first one — no separate branch to forget when
the execution path changes later.

**`FAILED`/`TIMED_OUT` stay distinct from `PERMANENTLY_FAILED`.** The transient states are where the
retry decision happens. Collapsing them makes *"was this retried, and how often?"* unanswerable
from the data.

**Illegal transitions raise, never warn.** A silently accepted illegal transition means your
recorded state no longer describes reality, and every decision built on it afterwards is wrong —
including the trace you would use to debug it.

**Test first, and this is the highest-value test file in the project:**
```python
LEGAL: set[tuple[TaskState, TaskState]] = { ...11 pairs... }

@pytest.mark.parametrize(("cur","tgt"), [p for p in itertools.product(TaskState, TaskState)
                                          if p not in LEGAL])
def test_every_other_transition_raises(cur, tgt): ...   # all 53 of them

def test_the_legal_set_is_exactly_what_the_module_permits(): ...
```

**Why that last test exists** — without it, adding a transition to the module silently shrinks the
illegal set and nobody notices. It keeps the table and the test from drifting apart.

Testing only the 11 legal transitions tests almost nothing. **The value is in the 53.**

**Answer key** — `git show v0.4:src/amos/orchestration/state.py`

---

### 2. `src/amos/orchestration/plan.py` (~138 lines)

**Why now** — a plan comes from an LLM, so it is untrusted structure. Decide what a valid one is
before anything can produce one.

```python
MAX_TASKS = 10; MAX_DEPENDENCIES = 5

class PlannedTask(BaseModel):
    id: str = Field(min_length=1, max_length=32)      # "t1", not a UUID — see below
    description: str = Field(min_length=1, max_length=500)
    depends_on: list[str] = []

class Plan(BaseModel):
    reasoning: str = ""
    tasks: list[PlannedTask] = Field(min_length=1, max_length=MAX_TASKS)

    @model_validator(mode="after")
    def _validate_graph(self) -> Plan:
        # unique ids; every dependency resolves; no self-dependency; NO CYCLES

    def topological_order(self) -> list[PlannedTask]: ...

def _find_cycle(tasks) -> list[str] | None:
    """ITERATIVE DFS with an explicit stack. Returns the actual cycle."""
```

**Invariant — validate before persisting.** A cyclic plan that reached the database would be a run
that can never complete, holding rows that look live forever.

**Why ids are `t1` and not UUIDs** — the model must reference them in `depends_on`. Short symbolic
ids are far more reliable for it than UUIDs, which it will invent or mistype.

**Why cycle detection is iterative, not recursive** — a confused plan should be *rejected*, not
blow the Python stack. A crash is a worse failure mode than a rejection.

**Why it returns the cycle rather than a boolean** — the planner's repair prompt can then say
`t1 -> t2 -> t1`, which is actionable. A bare "invalid plan" is not.

**Test first** — a two-cycle, a three-cycle, and **a cycle hidden behind a valid prefix** (the one
a naive check misses). Plus a **diamond** (`t1 → {t2,t3} → t4`) which must be *accepted* — it is not
a cycle, and getting that wrong is common.

---

### 3. `src/amos/orchestration/retry.py` (~60 lines)

```python
def backoff_delay(attempt: int, *, base=0.5, cap=30.0, jitter=True, rng=None) -> float: ...
def should_retry(attempt_count: int, max_attempts: int) -> bool: ...
```

Three properties, each load-bearing:

**Bounded** — a retry budget is a cost ceiling. Unbounded retries against a daily quota burn the
day on one broken task.

**Exponential** — an overloaded dependency needs room to recover.

**Jittered** — the one that is easy to skip. Five tasks fail at the same instant. Without jitter all
five wait exactly 0.5s, retry together, fail together, wait exactly 1.0s, and collide again —
**forever, in lockstep**. Backoff spaces attempts in *time* but not across *tasks*, so it recreates
the burst that caused the failure.

**Test first, and write this one specifically:**
```python
def test_jitter_actually_varies():
    """Without this, 'jitter' could be a constant and pass every bounds test
    while doing nothing to decorrelate retries."""
```

The budget is **per task**, not per run. One flaky task must not consume its siblings' allowance.

---

### 4. `src/amos/orchestration/planner.py` (~128 lines)

```python
class Planner:
    async def plan(self, goal: str, calls: list[LLMCallRecord] | None = None) -> Plan:
        # bounded attempts; on rejection, append the SPECIFIC error and re-prompt
        # exhausted -> PlanningError
```

Record **every** attempt in `calls`, including rejected ones. A rejected plan still cost tokens.

The system instruction should push for the *fewest* tasks that do the job, and for **self-contained
descriptions** — the executor runs one task at a time and the agent cannot see the others, so
"summarise the above" is useless.

---

### 5. `src/amos/orchestration/executor.py` (~277 lines) — **no LLM calls in this file**

That constraint is the point. The executor decides what may run, what is retried, what is skipped
and when the run is over. The agent it delegates to does the reasoning.

```python
class TaskRunner(Protocol):
    async def run(self, goal: str) -> AgentResult: ...     # deliberately narrow

@dataclass
class TaskExecution:
    plan_ref: str; description: str; depends_on: list[str]; position: int
    state: TaskState = TaskState.PENDING
    attempt_count: int = 0
    def transition(self, target: TaskState) -> None:
        self.state = assert_transition(self.state, target)   # THE ONLY WAY STATE CHANGES

class Executor:
    async def execute(self, plan: Plan) -> ExecutionReport:
        # while True:
        #   self._skip_unreachable(tasks)     ← to a FIXED POINT
        #   self._promote_ready(tasks)
        #   runnable = [t for t in tasks.values() if t.state is READY]
        #   if not runnable: break
        #   await asyncio.gather(*(self._run_task(t, tasks) for t in runnable))
```

**Why `_skip_unreachable` repeats until stable** — skipping propagates. If `t3` depends on `t2`
depends on failed `t1`, then `t3` must be skipped too. One pass leaves it waiting forever on
something that will never move.

**Why `asyncio.gather` over ready tasks** — this is where a DAG earns its complexity over a list.
Tasks with no dependency on each other do not wait for each other.

**Termination** — each iteration either advances a task toward terminal or finds nothing runnable.
Assert at the end that every task is terminal; if that ever fires, the loop has a hole.

**`_build_goal`** passes upstream results to dependents, because each description is self-contained
and the agent remembers nothing between tasks.

**Run outcomes**
```python
COMPLETED             # all succeeded
PARTIALLY_COMPLETED   # some did, some did not
FAILED                # none did
```

**`PARTIALLY_COMPLETED` is not a consolation prize.** A research goal where three sources answered
and one timed out produced real value. Forcing that into binary means discarding good work or
overstating what happened.

**Test first**

| Test | Trap |
|---|---|
| dependencies run before dependents | a DAG walked as a list |
| independent tasks run together | false serialisation |
| failing task retried then **permanently** failed | unbounded retry |
| **skipping propagates transitively** | `t3` waiting forever |
| an unrelated branch still runs | failure that spreads too far |
| `PARTIALLY_COMPLETED` is produced | binary success/failure |
| every task ends terminal | a hole in the loop |

Inject `sleep` so retry tests do not actually wait for backoff.

---

### 6. `src/amos/orchestration/orchestrator.py` (~186 lines)

Composes planner + executor + synthesis, and satisfies **`run(goal) -> AgentResult`** — the same
interface as `GroundedAgent` and `ToolUsingAgent`.

That is why `RunService` needs no changes. Keep that interface stable and V0.7 will slot an entire
agent team in behind it.

**Two cost decisions:**

**Skip synthesis when a single task ran.** Its answer *is* the answer; paying a call to rephrase it
is waste. On a 20-request/day tier that is a measurable fraction of the day.

**Skip synthesis when nothing succeeded.** There is nothing to combine; assemble the failure
response from the recorded errors.

---

### 7. The `tasks` table

Add it to `models.py` with a migration.

```python
depends_on: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), ...)
```

**Why a Postgres array and not a join table** — the textbook answer is a `task_dependencies` table,
but every read of this graph loads a run's tasks together anyway, so the join buys nothing and
costs a table on the hottest path.

Store **row UUIDs**, not the planner's symbolic refs, so the graph survives without the plan text.

⚠️ **Resist adding `claimed_at` "for V0.8".**

<details><summary>Why</summary>

The original added it here, reasoning that adding a column later to a populated table is a
migration. Sound reasoning, wrong guess — V0.8 claims at the **run** level, not the task level. The
column was never used and had to be removed.

A speculative column is worse than a missing one, because **a column in a schema looks like a
decision somebody made for a reason.**
</details>

---

## Checkpoint

```bash
.venv/bin/python -m pytest -q      # ~266

curl -s -X POST localhost:8000/v1/goals -H 'content-type: application/json' \
  -d '{"goal":"Work out 17% of 2340 and 23% of 1500, then say which is larger and by how much."}' \
  | jq '.outcome, .tasks, .tool_outcomes'
```

You should see a **diamond**: `t1` and `t2` with no dependencies, `t3` depending on both. Three
calculator calls. Answer 397.8 vs 345, larger by 52.8.

If the planner emits three *sequential* tasks, it has produced a list. That is worth investigating
before moving on.

---

## What this unlocks

Durable task state means V0.8 has something to claim. The `TaskRunner` Protocol means V0.7 can
substitute an agent team without the executor noticing.

Next: [`05-retrieval.md`](05-retrieval.md) — RAG, and the trap that silently degrades retrieval
without raising anything.
