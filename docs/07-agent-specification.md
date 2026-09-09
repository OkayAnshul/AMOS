# 07 — Agent Specification

**Written at V0.7**, when there was more than one agent. A specification describing how agents
differ is meaningless while there is exactly one, which is why this was a stub for six milestones.

## What an agent is

```
AgentSpec
├── name                 stable identifier the router chooses
├── purpose              one line, shown to the router
├── tools                ALLOWLIST — enforced by construction
├── system_instruction   role and constraints
├── max_iterations       bound on its tool loop
└── routing_hint         phrasing that typically indicates this agent
```

## What makes an agent "specialised"

**Three prompts over the same toolset is not a multi-agent system.** It is one agent with a mood
ring, and it is the first thing a skeptical interviewer probes.

The property that makes these agents genuinely different is a **tool allowlist enforced in code**.
`AgentSpec.registry_from()` builds a `ToolRegistry` containing only that agent's permitted tools,
so a Researcher asking for `calculator` gets `NOT_FOUND` — not because it was instructed not to,
but because the tool does not exist inside its registry.

Specialisation therefore has the same standing as every other guarantee in AMOS: a property of the
code, not of the prompt.

## The agents

| Agent | Tools | Cannot |
|---|---|---|
| **Researcher** | `search_knowledge`, `http_get`, `read_file`, `recall_facts`, `recall_past_runs` | compute anything |
| **Analyst** | `calculator` | search, fetch or recall |
| **Critic** | *(none)* | do anything but judge |

The two routable allowlists are **disjoint**. That is deliberate: overlapping capability makes
routing arbitrary, because either agent could do the work.

### Why the critic has no tools

A critic that can fetch new sources is doing research, and its verdict becomes **unfalsifiable** —
it can always go and find something that justifies whatever it already concluded. Judging only
what it was given is what makes the judgement mean anything.

It is also excluded from routing: assigning work to a validator is a category error, not a
routing preference.

### Instructions must match allowlists

A prompt promising a capability the allowlist denies produces an agent that repeatedly attempts a
tool it cannot have, burning iterations. So the Researcher's instruction says *"You have no
calculator"* and the Analyst's says *"You cannot search, fetch or recall anything."*
`test_instructions_reflect_the_allowlist` asserts this.

## Inter-agent communication

Structured objects, never prose (project brief §10):

```python
AgentTask(task_id, source_agent, target_agent, instruction, context)
CriticReport(verdict, reasoning, unsupported_claims, missing_from_answer)
RoutingDecision(agent, reason)
```

A natural-language handoff — *"hey, can you check the flight prices"* — is unparseable,
unvalidatable and untestable. When the receiver misunderstands, there is nothing to point at: no
field was wrong, because there were no fields. Structured contracts make the failure mode
**validation** rather than **misinterpretation**.

`CriticReport.unsupported_claims` is the field carrying the work. A verdict alone gives the
producer nothing to act on; naming the claims that lack support does.

## Routing

A small, zero-temperature LLM call whose output is **validated against the registry**. Routing
should be stable, not creative — the same task should route the same way every time.

A hallucinated agent name **falls back** rather than failing. Routing to the wrong agent produces
a worse answer; failing the task produces none. A wrong route is recoverable; a dead task is not.

Routing is skipped entirely when fewer than two agents could apply — there is nothing to choose
between, and the call would be pure cost.

### Measured

**100% (10/10)** on the labelled set, after a fix the measurement itself surfaced.

The first run scored 90%. The miss — *"Check whether previous runs solved a similar goal"*, which
I labelled `analyst` and the router sent to `researcher` — was **my error, not the router's**:
recalling a past run is a lookup, structurally identical to searching documents. The tool moved to
the researcher and the label followed the fix.

Reproduce: `python -m amos.agents.cli`

**What the number does not show:** ten cases, written by the person who wrote the agents, with
both the system and one label changed between runs. The defensible claim is *"routing is correct
on a small labelled set after a tool-assignment fix the measurement surfaced"* — not "routing is
100% accurate."

## The critic loop

```
answer → review → accept?  → done
                → revise?  → producer revises (bounded) → review again
                           → budget exhausted → return answer WITH objections
```

**The bound is the guarantee.** Critic and producer can disagree forever; a critic that always
finds an objection paired with a producer that never satisfies it spends a day's quota in minutes.
`max_revisions` (default 1) caps it in code, exactly as V0.4 caps the tool loop.

When the budget is exhausted with objections outstanding, the answer is returned **with the
objections attached as caveats and confidence downgraded to low** — not discarded, and not
silently presented as accepted. A rejected answer returned as though accepted is worse than either
accepting it openly or refusing.

**A broken critic accepts.** If the reviewer itself fails, blocking a correct answer is a worse
outcome than letting an unreviewed one through. The critic is a quality gate, not a correctness
requirement.

## Cost

| Step | Calls |
|---|---|
| Routing | 1 per task (skipped when only one agent applies) |
| Agent execution | 1–N per task |
| Critic review | 1 per answer, plus 1 per revision |

On a 20-request/day quota that is material, which is why `AMOS_MULTI_AGENT_ENABLED` and
`AMOS_CRITIC_ENABLED` exist. Measured on the live demo: a researched, reviewed answer cost
**4 calls / 3100 tokens**.

## Not done

- **No agent-to-agent delegation.** The orchestrator assigns tasks; agents do not hand work to
  each other. `AgentTask` exists and is currently only used for the shape.
- **No dynamic agent creation** — the roster is fixed at startup.
- **No per-agent memory.** All agents share one semantic store.
- **No critic specialisation** — one critic judges every kind of answer.
- **Routing measured on 10 self-authored cases**, which cannot distinguish a good router from a
  set of easy questions.
