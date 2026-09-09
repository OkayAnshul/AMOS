# Interview — Multi-Agent (V0.7)

**Advance gate: V0.8 does not begin until these can be answered unaided.**

This is the milestone where "multi-agent" becomes claimable. It is not claimed anywhere before it.

---

### Your three agents — aren't they just three prompts?

That is the right question, and the answer is what the milestone is built around.

They differ in **capability, enforced by construction**. `AgentSpec.registry_from()` builds each
agent a `ToolRegistry` containing only its allowlisted tools. A Researcher asking for `calculator`
gets `NOT_FOUND` — not because its prompt discourages it, but because the tool is not in its
registry.

The two routable allowlists are **disjoint**: Researcher gets retrieval and recall, Analyst gets
the calculator. That is deliberate — overlapping capability makes routing arbitrary, because
either agent could do the work.

`test_researcher_cannot_calculate` and `test_analyst_cannot_reach_the_network` assert it
structurally.

### Why does the critic have no tools at all?

Because a critic that can fetch new sources is doing research, and its verdict becomes
**unfalsifiable** — it can always go and find something that justifies whatever it concluded.
Judging only the evidence it was handed is what makes the judgement mean anything.

The evidence it receives is also restricted to what the agent *actually retrieved*
(`_evidence_from`). Giving it more would let it validate an answer against sources the answer
never saw.

### What stops the critic and the producer arguing forever?

`max_revisions`, checked by the loop in code. A critic that always finds an objection, paired with
a producer that never satisfies it, is an infinite loop that spends a day's quota in about ten
minutes.

Same principle as V0.4's tool-loop cap: **the bound is the guarantee, the prompt is a request.**

### What happens when the revision budget runs out and objections remain?

The answer is returned **with the objections attached as caveats and confidence downgraded to
low**. Not discarded, not silently presented as accepted.

Three options, and why this one: discarding throws away work that is probably partially correct;
returning it silently is a lie the user cannot detect; returning it with the objections lets them
judge. `test_unresolved_objections_reach_the_user` asserts the caveats survive.

### The critic itself fails. Then what?

It accepts. The critic is a **quality gate, not a correctness requirement** — if the reviewer
breaks, blocking a correct answer is a worse outcome than letting an unreviewed one through.
Failing the run because the *optional* validator failed trades a real answer for a process
complaint.

### Why does a hallucinated agent name fall back instead of raising?

Routing to the wrong agent produces a worse answer. Failing the task produces none. **A wrong
route is recoverable; a dead task is not.**

The fallback is logged, so a router that is frequently wrong is visible rather than silently
absorbed.

### How do you know routing actually works?

It is measured: **100% (10/10)** on a labelled set — `python -m amos.agents.cli`.

The interesting part is the first run, which scored 90%. The miss was *"Check whether previous
runs solved a similar goal"* — I labelled it `analyst`, the router chose `researcher`.

**The router was right and my design was wrong.** I had put `recall_past_runs` on the analyst,
reasoning that past outcomes inform judgement. But recalling a past run is a *lookup* —
structurally identical to searching documents. The tool moved, and the label followed the fix.

The measurement caught an error in my design, not in model behaviour, which is not what I built it
to find.

### Isn't 10/10 after changing a label exactly the metric-massaging you warned about in V0.5?

It has the same shape, and it deserves the same scrutiny. The distinction I would defend:

- In V0.5 I changed the **ground truth** to match the system's output.
- Here I changed the **system** (which agent owns a tool) because the disagreement revealed a real
  design error, and the label followed the code.

But the honest caveat stands: **ten self-authored cases cannot distinguish a good router from a
set of easy questions.** The defensible claim is "routing is correct on a small labelled set after
a fix the measurement surfaced" — not "routing is 100% accurate."

### Why structured messages between agents rather than natural language?

A prose handoff is unparseable, unvalidatable and untestable. When the receiver misunderstands
there is nothing to point at: no field was wrong, because there were no fields.

Structured contracts turn the failure mode from **misinterpretation** into **validation** — one
you catch, one you have to notice.

Concretely: `CriticReport.unsupported_claims` is what makes a rejection actionable. A verdict alone
gives the producer nothing to work with; naming the specific unsupported claims does.

### Adding a whole agent layer — how much did that break?

Nothing. `AgentTeam` satisfies `run(goal) -> AgentResult`, the same interface as every agent since
V0.1, so the executor, orchestrator and `RunService` are untouched.

Five milestones of that interface holding is why V0.7 is a substitution rather than a rewrite —
and it traces directly back to the seams built in V0.1 for reasons that had nothing to do with
multi-agent.

### What does it cost?

Routing adds one call per task; criticism adds one per answer plus one per revision. Measured on
the live demo: a researched, reviewed answer cost **4 calls / 3100 tokens** — against a 20/day
quota.

Which is why `AMOS_MULTI_AGENT_ENABLED` and `AMOS_CRITIC_ENABLED` exist, and why routing is
skipped when fewer than two agents could apply.

---

## What V0.7 does NOT demonstrate

- **No agent-to-agent delegation.** The orchestrator assigns work; agents do not hand tasks to
  each other. `AgentTask` exists but currently only defines the shape
- **No dynamic agent creation** — the roster is fixed at startup
- **No per-agent memory** — all agents share one semantic store
- **One critic for every kind of answer**, with no specialisation
- Routing measured on **10 self-authored cases**
- Still synchronous: no worker, no queue. `SKIP LOCKED` is V0.8
- Still not idempotent; still no crash resumption
