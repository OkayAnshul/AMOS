# V0.7 — Multi-agent

**+1,336 lines.** The milestone where "multi-agent" becomes a word you can defend.

Note where this sits: **it is not claimed anywhere before here.** Six milestones of resume evidence
say "not built" on that row, because one agent with three prompts is not a multi-agent system — and
that is the first thing an interviewer probes.

---

## Where you are

One agent that plans, uses tools, retrieves and remembers. Every goal goes through the same agent
with the same capabilities.

## The problem

The prompt is doing too much work. One instruction has to cover finding information, computing over
it, and judging whether the answer holds up — and nothing stops the agent doing any of those badly
in a way nothing checks.

## The question you must be able to answer

> *"Your three agents — aren't they just three prompts?"*

If your answer is about wording, you have not built a multi-agent system. **The answer has to be
about capability, enforced by construction.**

---

## The decision that makes it real

Each agent is built with a `ToolRegistry` containing **only its allowlisted tools**. A Researcher
asking for `calculator` gets `NOT_FOUND` — not because its prompt discourages it, but because the
tool does not exist inside its registry.

Specialisation then has the same standing as every other guarantee here: a property of the code.

| Agent | Tools | Cannot |
|---|---|---|
| **Researcher** | `search_knowledge`, `http_get`, `read_file`, `recall_facts`, `recall_past_runs`, **`remember_fact`** | compute |
| **Analyst** | `calculator` | search, fetch or recall |
| **Critic** | *(none)* | anything but judge |

**The two routable allowlists are disjoint, deliberately.** Overlapping capability makes routing
arbitrary — either agent could do the work, so choosing between them means nothing.

⚠️ **Note `remember_fact` on the researcher, and do not omit it.**

<details><summary>The original did, and it cost a measured 0% store rate</summary>

Written at V0.7, `remember_fact` was left out of all three allowlists. With routing enabled — the
default — every specialist's registry filtered it out and storing a fact was **structurally
impossible**. Measured after the fact: 0% store rate, 38% false claims.

`test_all_tools_are_registered_when_a_database_is_present` passed throughout, because it checks the
**global** registry. **Registration and reachability are different properties.**

It belongs with the researcher because that agent already owns every memory *read*; splitting reads
and writes across agents would mean "what did I tell you, and also remember this" cannot be done by
one agent.
</details>

---

## Build order

### 1. `src/amos/agents/registry.py` (~166 lines)

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str
    purpose: str                      # shown to the router
    tools: frozenset[str]             # ENFORCED BY CONSTRUCTION
    system_instruction: str
    max_iterations: int = 5
    routing_hint: str = ""            # written for a classifier, not a human

    def registry_from(self, available: ToolRegistry) -> ToolRegistry:
        """Only this agent's permitted tools. Anything else is simply ABSENT,
           so refusing it needs no new enforcement path."""

RESEARCHER = AgentSpec(...); ANALYST = AgentSpec(...); CRITIC = AgentSpec(...)

@dataclass
class AgentRegistry:
    def get(self, name) -> AgentSpec:  """Raises naming the available agents."""
    @property
    def routable(self) -> list[AgentSpec]:  """Excludes the critic."""
```

**Why the critic has no tools at all** — a critic that can fetch new sources is doing research, and
its verdict becomes **unfalsifiable**: it can always find something justifying whatever it already
concluded. Judging only what it was handed is what makes the judgement mean anything.

It is also excluded from routing — assigning work to a validator is a category error, not a routing
preference.

**Instructions must match allowlists.** A prompt promising a capability the allowlist denies
produces an agent that repeatedly attempts a tool it cannot have, burning iterations. So the
Researcher's says *"You have no calculator"* and the Analyst's says *"You cannot search, fetch or
recall anything."*

**Test first**
```python
def test_agents_have_different_tool_allowlists():
    assert RESEARCHER.tools.isdisjoint(ANALYST.tools)   # if equal, this is ONE agent

def test_researcher_cannot_calculate(): ...    # structurally, via registry_from
def test_the_critic_has_no_tools_at_all(): ...
def test_instructions_reflect_the_allowlist(): ...
def test_every_registered_tool_is_reachable_by_at_least_one_agent(): ...   # ← the trap above
```

---

### 2. `src/amos/agents/messages.py` (~60 lines)

```python
class AgentTask(BaseModel):
    task_id: str; source_agent: str; target_agent: str
    instruction: str; context: list[str] = []

class CriticReport(BaseModel):
    verdict: Verdict                          # accept | revise
    reasoning: str
    unsupported_claims: list[str] = []        # ← the field carrying the work
    missing_from_answer: list[str] = []

class RoutingDecision(BaseModel):
    agent: str; reason: str = ""
```

**Why structured and not prose** — a natural-language handoff (*"hey, can you check the flight
prices"*) is unparseable, unvalidatable and untestable. When the receiver misunderstands there is
**nothing to point at**: no field was wrong, because there were no fields.

Structured contracts turn the failure mode from **misinterpretation** into **validation** — one you
catch, one you have to notice.

**`unsupported_claims` is the field doing the real work.** A verdict alone gives the producer
nothing to act on; naming the specific claims does.

*Honest note:* `AgentTask` gets defined here and is **currently unused**. Agents do not delegate to
each other — the orchestrator assigns. That is a real limitation and the last unbuilt clause of the
original vision.

---

### 3. `src/amos/agents/router.py` (~184 lines)

```python
class Router:
    async def route(self, instruction, calls=None) -> RoutingDecision:
        # temperature=0.0 — routing should be STABLE, not creative
        # validate against the registry; unknown or "critic" -> fall back
```

**Why a hallucinated agent name falls back rather than raising** — routing to the wrong agent
produces a worse answer; failing the task produces none. **A wrong route is recoverable; a dead task
is not.** Log the fallback so a frequently-wrong router is visible rather than silently absorbed.

**Skip routing when fewer than two agents could apply.** Nothing to choose between; the call is pure
cost.

**And measure it** — otherwise "specialised agents" is three prompts and a hopeful story:

```python
@dataclass(frozen=True)
class RoutingCase:
    instruction: str; expected_agent: str; note: str = ""

async def evaluate_routing(router, cases) -> RoutingResult:
    """Mistakes record what was chosen INSTEAD."""
```

**Include cases whose *topic* suggests one agent and whose *requirement* suggests another** —
*"Calculate how many chunks 28 documents produce"* is about documentation and requires arithmetic. A
router that pattern-matches subject matter must fail those, or the measurement proves nothing.

### 🔎 Expect the measurement to catch **you**

The original scored 90%, and the single miss was *"Check whether previous runs solved a similar
goal"* — labelled `analyst`, routed to `researcher`.

**The router was right.** `recall_past_runs` had been put on the analyst, reasoning that past
outcomes inform judgement. But recalling a past run is a **lookup** — structurally identical to
searching documents. The tool was on the wrong agent.

Moving it took routing to 10/10 *and* made the allowlists fully disjoint, which is a better design
independent of the score.

**Caveat that must travel with the number:** ten self-authored cases cannot distinguish a good
router from a set of easy questions. And changing both the system and a label between runs is the
same shape as massaging a metric — the defensible claim is *"correct on a small labelled set after
a fix the measurement surfaced"*, not *"100% accurate"*.

---

### 4. `src/amos/agents/critic.py` (~143 lines)

```python
class Critic:
    async def review(self, goal, answer, evidence, calls=None) -> CriticReport:
        """NEVER raises. A failed review ACCEPTS."""

def apply_report(answer: AgentResponse, report: CriticReport) -> AgentResponse:
    """Attach unresolved objections as caveats. Downgrade confidence to low."""
```

**Why a broken critic accepts** — it is a **quality gate, not a correctness requirement**. If the
reviewer itself breaks, blocking a correct answer is worse than passing an unreviewed one. Failing
the run because the *optional* validator failed trades a real answer for a process complaint.

**Why confidence is downgraded rather than the claim rewritten** — editing the model's prose risks
changing its meaning; an explicit contradiction below it is unambiguous and auditable. And an answer
carrying unresolved objections cannot honestly stay "high confidence".

⚠️ `model_copy(update=...)` **does not re-validate.** Pass the enum member, not its value, or you
get a `str` in a field annotated as an enum — invisible until something does `is Confidence.LOW`.

---

### 5. `src/amos/agents/team.py` (~198 lines)

```python
class AgentTeam:
    """Satisfies run(goal) -> AgentResult, like every agent since V0.1."""
    def agent_for(self, spec: AgentSpec) -> ToolUsingAgent:
        return ToolUsingAgent(self._provider, spec.registry_from(self._tools),
                              system_instruction=spec.system_instruction, ...)

    async def run(self, goal) -> AgentResult:
        spec = await self._choose(goal, calls)
        result = await self.agent_for(spec).run(goal)
        if self._critic_enabled:
            answer, report, review_calls = await self._review_and_revise(...)
```

**The revise loop must be bounded in code.** Critic and producer can disagree forever; a critic that
always finds an objection paired with a producer that never satisfies it spends a day's quota in
about ten minutes. Same principle as V0.4's loop cap: **the bound is the guarantee, the prompt is a
request.**

**When the budget is exhausted with objections outstanding, return the answer WITH the objections
attached** — not discarded, not silently presented as accepted. Three options, and the other two
are "throw away work that is probably partly right" and "tell the user a lie they cannot detect".

**Evidence given to the critic is only what the run actually retrieved.** Anything more lets it
validate against sources the answer never saw.

**Test first**

| Test | Trap |
|---|---|
| the specialist is offered **only** its own tools | specialisation by prompt |
| an invalid agent name falls back | a dead task |
| a critic that never accepts **cannot loop forever** | unbounded argument |
| unresolved objections reach the user | a silent lie |
| the critic can be disabled | cost on a small quota |
| routing + review costs appear in the trace | invisible spend |

---

### 6. Wire it in — and notice how little changes

`AgentTeam` satisfies `run(goal) -> AgentResult`. So the executor, the orchestrator and
`RunService` are **untouched**.

Five milestones of that interface holding is why V0.7 is a substitution rather than a rewrite — and
it traces back to a Protocol defined in V0.1 for a reason that had nothing to do with multi-agent.

Also: delete anything that has never fired. The original removed a `_finalise` fallback here, dead
across four milestones. **Untested-in-practice code is a liability, not a safety net.**

---

## Checkpoint

```bash
.venv/bin/python -m amos.agents.cli        # routing accuracy — measure it

-d '{"goal":"Calculate 55% of 320."}'
# routed to: analyst   tools: [calculator]

-d '{"goal":"Find what the docs say about the visibility timeout."}'
# routed to: researcher   tools: [search_knowledge]   critic: accept
```

And re-run the V0.6 memory checkpoint. If storing a fact now fails, you have hit the allowlist trap.

---

## What this unlocks

Nothing structural. But you can now say "multi-agent" and defend it — and you have a routing number
rather than an assertion.

Next: [`08-async.md`](08-async.md) — the densest distributed-systems content in the project, and the
one with the most opportunities to overclaim.
