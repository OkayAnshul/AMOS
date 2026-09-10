# Build AMOS by hand

A file-by-file guide to writing AMOS yourself: **what to type, in what order, and why that file
now rather than later.**

This exists because the project set two equal objectives on day one and only one of them is done:

> 1. Build AMOS.
> 2. Understand it well enough to modify, debug, explain, extend and defend it.
>
> *"Objective 2 is not decoration. A milestone is not done if only objective 1 is met."* — `CLAUDE.md`

---

## How this works

**You get contracts, not code.** Each file's section gives you its purpose, its signatures, the
invariants it must hold, and the decision behind it. **You write the body.**

That is deliberate. The finished code is already in this repository — a guide that reprinted it
would be a typing exercise, and typing is not understanding. The interesting question is never
*"what does this line say"*, it is *"why does this file exist, and why now"*.

### The answer key is the git history

Every milestone is tagged. Check your own work without reading ahead:

```bash
git show v0.1:src/amos/config.py     # the exact file as it existed at V0.1
git diff v0.1 v0.2 -- src tests      # precisely what V0.2 added
git log v0.3 --oneline               # the commits that got there
```

Use it **after** you have written something and it does not work — not before you start.

### Build in a separate directory

```bash
mkdir ~/amos-by-hand && cd ~/amos-by-hand && git init
```

Do not type into this repository. You want the original intact as your reference.

---

## What it costs

**~14,000 lines of your own typing** — 8,732 of source, 5,244 of tests.

Realistically **60–100 hours** if you are thinking about each file rather than transcribing it.
Anyone who tells you this is a weekend is lying, and you would find out at hour thirty.

| Milestone | What it adds | Lines |
|---|---|---|
| V0.1 Foundation | 15 source + 11 test files, from nothing | — |
| V0.2 Tools | +1,836 |
| V0.3 Persistence | +1,241 |
| **V0.4 Orchestration** | **+1,902** ← largest |
| V0.5 Retrieval | +1,877 |
| V0.6 Memory | +1,191 |
| V0.7 Multi-agent | +1,336 |
| V0.8 Async | +994 |
| V0.9 Observability | +518 ← smallest, and that is a lesson in itself |
| V1.0 Evaluation | +1,023 |

Measured with `git diff --shortstat` between tags, not estimated.

---

## You do not have to build all of it

Every milestone is a stopping point by design (`../19-roadmap.md`). Each leaves a repository that
runs, is tested, and can be shown to somebody.

| Stop at | What you have | Cost |
|---|---|---|
| **V0.3** | A typed, tool-using agent whose every run is durable and inspectable. A real backend project. | ~30% |
| **V0.5** | The above plus retrieval with citations and a measured recall figure. The strongest single interview story here. | ~55% |
| **V1.0** | Everything, including async workers, tracing and an evaluation harness. | 100% |

**If you stop, do not stop before V0.4.** It is the largest milestone and it carries the most
transferable ideas — state machines, DAGs, exponential backoff with jitter, failure containment.
Those come up in interviews about systems that have nothing to do with AI.

---

## The order

| | Guide | Milestone |
|---|---|---|
| 0 | [`00-setup.md`](00-setup.md) | Environment, `pyproject.toml`, the decisions you inherit |
| 1 | [`01-foundation.md`](01-foundation.md) | V0.1 — a typed LLM service, **no database** |
| 2 | [`02-tools.md`](02-tools.md) | V0.2 — tools the agent chooses, and the security boundary |
| 3 | [`03-persistence.md`](03-persistence.md) | V0.3 — every run durable and inspectable |
| 4 | [`04-orchestration.md`](04-orchestration.md) | V0.4 — planner, task DAG, state machine |
| 5 | [`05-retrieval.md`](05-retrieval.md) | V0.5 — RAG, measured |
| 6 | [`06-memory.md`](06-memory.md) | V0.6 — facts that outlive the process |
| 7 | [`07-multi-agent.md`](07-multi-agent.md) | V0.7 — specialists, routing, a critic |
| 8 | [`08-async.md`](08-async.md) | V0.8 — workers, `SKIP LOCKED`, crash recovery |
| 9 | [`09-observability.md`](09-observability.md) | V0.9 — OpenTelemetry |
| 10 | [`10-evaluation.md`](10-evaluation.md) | V1.0 — measuring your own quality |

---

## Three rules

**1. Write the test first, where the guide says to.** Not dogma — in this project the tests are
where the lessons are. 53 illegal state transitions teach more than the 11 legal ones. A test that
asserts jitter *varies* catches a constant that passes every bounds check.

**2. Do not skip the checkpoint.** Each milestone ends with a command and its expected output. If
it does not pass, the next milestone builds on something broken. This project shipped a package
that could not be imported while 41 tests passed — running the thing is not the same as testing it.

**3. When you hit a trap, sit with it for ten minutes.** Each milestone lists the traps you will
hit, symptom first, with the cause folded away. Ten minutes stuck teaches more than the fix does. A
lost afternoon does not.

---

## Related documents

| | |
|---|---|
| [`../24-study-plan.md`](../24-study-plan.md) | What to **study**, tier by tier, with algorithms at `file:line` |
| [`../25-build-journal.md`](../25-build-journal.md) | How it was **actually built** — 12 chapters, including 22 things believed that were false |
| [`../03-architecture-decisions.md`](../03-architecture-decisions.md) | The 8 ADRs, each with an explicit *Reconsider if* |
| [`../../engineering/bugs-log.md`](../../engineering/bugs-log.md) | 24 bugs with the lesson each taught. Where the traps come from |
| [`../interview/`](../interview/) | 10 documents of questions. **This is the gate** — the point of all of it |

If you read only one before starting, read the **build journal**. It is the only document written
to be read start to finish, and it will tell you what you are in for.
