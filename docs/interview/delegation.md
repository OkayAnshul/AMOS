# Interview — Agent-to-Agent Delegation (V1.3)

**Advance gate: V1.4 does not begin until these can be answered unaided.**

The milestone that gave `AgentTask` a caller after six milestones of being defined and unused.
Note what is still *not* claimed: agents do not negotiate, there is no shared blackboard, and the
orchestrator still assigns the top-level work.

---

### What was actually missing before V1.3?

A Researcher retrieves *"the quota is 20 requests per day"*, is asked for 15% of it, and **cannot
do the arithmetic** — `calculator` is not in its allowlist.

Its options were both bad: compute in its head, which is the exact failure tools exist to prevent
and which the Analyst's own instruction forbids; or fail a goal it had nearly completed.

The pressure this creates is the interesting part. Without delegation, the natural fix is to widen
the Researcher's allowlist — and once allowlists overlap, **routing accuracy stops meaning
anything**, because either agent could have done the work. Delegation is what lets specialisation
stay strict.

### Why is delegation a tool rather than a new mechanism?

Because a tool already provides everything delegation needs, and the system already trusts that
machinery:

- arguments are **schema-validated before anything executes** — they came from a model, so they
  are untrusted input
- a timeout
- a span and a `tool_calls` row, so a delegation appears in the trace like any other action
- an outcome the model can see and react to, including a refusal
- **per-agent allowlisting** — so "which agents may delegate" is the same mechanism as "which
  agents may search", with no second enforcement path

This is the same argument that made retrieval a tool at V0.5. The alternative — an extra LLM turn
asking "do you need help?" — spends a call per task to answer a question that is usually no, which
on a 20-request/day quota is the difference between six goals and three.

### What stops delegation recursing forever?

**Depth, and it is enforced by absence.** A delegate built at the cap gets a registry that does
not contain `delegate`. There is no check to argue past — the tool does not exist, so a call
returns `NOT_FOUND` through machinery that never reads model output.

That is also why cycles need no separate detection. researcher → analyst → researcher terminates
because the second hop has no `delegate` tool, not because anything noticed a cycle.

Contrast with the shape that would have been wrong: a check *inside* the tool that compares depth
and refuses. It would work, and it would be a guarantee that lives in code the model's output
flows into — the thing `docs/13-security.md` says never to rely on.

### Why is the budget shared rather than per agent?

Because a per-agent counter is not a budget. Three agents with three delegations each is nine
delegations, and the cost is a property of the **run**, not of any one agent.

It is a mutable object passed down the chain, deliberately: every `DelegateTool` in a run points
at the same counter.

### What happens when the budget runs out?

The tool returns a **refusal the model can act on** — `delegated: false`, with a reason saying the
budget is spent and to answer with what it has. Not an exception.

The distinction matters: an exception would fail the task, discarding work that was probably
nearly complete. A refusal lets the caller finish honestly and say what is missing, which is the
same pattern as empty retrieval returning a refusal instruction rather than an empty list.

### Does a delegate inherit the caller's tools?

**No, and this is the security-relevant answer.** The delegate is built from its own `AgentSpec`,
so it gets its own allowlist.

**Delegation moves work, not authority.** If it moved authority, it would be a
privilege-escalation path dressed as a feature: a prompt injection that persuaded a Researcher to
delegate would inherit whatever the Analyst can do, and the direction of that escalation would
depend only on which agent had the more useful tools.

A test asserts it directly — with delegation enabled, `calculator` is still absent from the
researcher's registry.

### Why does the system instruction change with depth?

Because `docs/07-agent-specification.md` already required that **instructions match allowlists** —
a prompt promising a capability the registry denies produces an agent that repeatedly attempts a
tool it cannot have, burning iterations against the loop cap.

Delegation makes that rule dynamic: the tool's presence depends on depth, so the prompt has to as
well. The delegation paragraph is appended only when the tool is actually in the registry.

The list of other agents inside it is built **from the registry**, not written into each spec, so
adding a fourth agent does not mean editing three prompts to mention it.

### How does a delegate's failure reach the caller?

As a failed tool outcome — `status` is not `OK` — exactly like any other tool failure. The
caller's model then decides what to do: retry differently, delegate to someone else within budget,
or answer without it.

That is the same trust boundary every tool has. Silently returning an empty answer would be worse
than failing, because the caller would present a gap as a result.

### There are now two ways work gets distributed. Is that not a design smell?

It is a real increase in surface area, and worth defending rather than waving away.

- **Planned decomposition** (V0.4): up front, deterministic, produces a durable DAG. Answers
  *what are the steps?*
- **Delegation** (V1.3): mid-task, model-decided, ephemeral. Answers *I have hit something I
  cannot do.*

The second cannot be replaced by the first, because the need is **discovered** during execution
rather than predictable before it. The planner does not know which sub-step will turn out to need
arithmetic.

What would be a smell: delegation deep enough to *replace* planning. That is why the depth default
is 1 and ADR-012 says a need for more than 2 suggests the planner should have decomposed instead.

### What does this cost, and when would you turn it off?

At least one LLM call per hop, plus the caller continuing its own loop with the result — so a
budget of 3 can triple a task's cost.

`AMOS_DELEGATION_ENABLED=false` exists for the same reason as `AMOS_PLANNING_ENABLED` and
`AMOS_CRITIC_ENABLED`: on a 20-request/day quota, every capability that costs calls needs a
switch, or development consumes the budget reserved for demonstrating the thing.

---

## What this milestone does *not* claim

- **No negotiation.** A delegate answers; it does not push back, ask clarifying questions, or
  refuse on grounds of its own judgement.
- **No shared state.** Everything the delegate needs must be in the instruction and context,
  because it genuinely cannot see the caller's work. That is a constraint, not an oversight — it
  is what keeps the contract checkable.
- **No delegation to the critic.** It is excluded from `routable`, and assigning work to a
  validator is a category error.
- **Not measured.** There is no delegation-accuracy number the way there is a routing-accuracy
  number. The bounds are tested; whether models *delegate well* is unmeasured, and saying so is
  more honest than quoting the golden-set pass rate as if it covered this.
