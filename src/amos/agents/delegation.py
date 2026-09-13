"""One agent handing work to another (V1.3, ADR-012).

`AgentTask` — the structured agent-to-agent contract the project brief's §10 asks
for — was defined at V0.7 and had no caller until this milestone. The orchestrator
assigned every task, and agents never spoke to each other.

The concrete gap it closes: a Researcher that retrieves "the quota is 20 requests
per day" and is then asked for 15% of it **cannot do the arithmetic**, because
`calculator` is not in its allowlist. Disjoint allowlists are what make routing
meaningful, so widening them is the wrong fix; its only other options were to
compute in its head — the exact failure tools exist to prevent — or to fail.

**Delegation is a tool.** The same argument that made retrieval a tool at V0.5:
schema-validated arguments, a timeout, a trace entry, a `tool_calls` row, an
outcome the model can react to, and per-agent allowlisting — all of it existing
machinery rather than a second enforcement path.

The bounds are structural, not prompted:

- **Depth** — a delegate is built with a registry that does not *contain*
  `delegate` once the cap is reached. Not a check it can argue past.
- **Budget** — a per-run ceiling on total delegations.
- **No self-delegation** — rejected as an invalid argument, before execution.

Cycles need no separate detection: researcher → analyst → researcher terminates
because depth is bounded, and depth is bounded by removing the capability.

The delegate runs with **its own** allowlist, never the caller's. Delegation
moves work, not authority — otherwise an injection that talked a Researcher into
delegating would inherit whatever the Analyst can do.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, Field

from amos.agents.messages import AgentTask
from amos.observability import log_event
from amos.tools.base import Permission, Tool

logger = logging.getLogger(__name__)

#: How many hops deep delegation may go. 1 means a delegate cannot delegate on.
#: More than 2 suggests the planner should have decomposed the goal instead
#: (ADR-012).
DEFAULT_MAX_DEPTH = 1

#: Total delegations allowed per run. Each hop is at least one LLM call against a
#: 20-request/day quota, and the caller then continues its own loop with the
#: result — so this is a cost ceiling, not a safety margin.
DEFAULT_BUDGET = 3


class SpecialistFactory(Protocol):
    """Builds a runnable agent for a named specialist, at a given depth.

    Depth is passed so the factory can decide whether that agent gets `delegate`
    in its registry. The tool never checks depth itself — it would be a check the
    model could be induced to talk past.
    """

    def __call__(self, name: str, *, depth: int) -> Any: ...


class DelegationBudget:
    """Shared, per-run count of how many delegations have happened.

    Deliberately mutable and shared between every `DelegateTool` in a run: the
    budget is a property of the run, not of one agent. A per-agent counter would
    let a chain of agents each spend the full allowance.
    """

    def __init__(self, limit: int = DEFAULT_BUDGET) -> None:
        self.limit = limit
        self.spent = 0

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit


class DelegateArgs(BaseModel):
    target_agent: str = Field(
        min_length=1,
        max_length=64,
        description="Which specialist should do this. Must be a different agent from you.",
    )
    instruction: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "A self-contained instruction. The other agent cannot see your "
            "conversation, so state everything it needs."
        ),
    )
    context: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Facts you have already established that it should use.",
    )


class DelegateTool(Tool):
    """Hand one piece of work to another specialist and return its answer."""

    name: ClassVar[str] = "delegate"
    description: ClassVar[str] = (
        "Hand a sub-problem to another specialist when it needs a capability you "
        "do not have - for example arithmetic when you can only search, or a "
        "lookup when you can only compute. Give a self-contained instruction and "
        "everything the other agent needs to know; it cannot see your work."
    )
    input_schema: ClassVar[type[BaseModel]] = DelegateArgs
    #: The delegate executes with its own allowlist, and every registrable tool
    #: is read-only, so delegating cannot reach further than the caller could
    #: have reached by asking for that agent directly.
    permission: ClassVar[Permission] = Permission.READ_LOCAL
    #: Generous: it wraps another agent's entire bounded loop, whose own timeout
    #: is the real bound. Must stay below the task timeout above it (ADR-009).
    timeout_seconds: ClassVar[float] = 120.0

    def __init__(
        self,
        caller: str,
        factory: SpecialistFactory,
        budget: DelegationBudget,
        *,
        depth: int = 0,
        available: frozenset[str] | None = None,
    ) -> None:
        self._caller = caller
        self._factory = factory
        self._budget = budget
        self._depth = depth
        self._available = available or frozenset()

    async def _run(self, args: DelegateArgs) -> dict[str, Any]:
        target = args.target_agent.strip().lower()

        if target == self._caller:
            # Not a loop guard — depth is that. This catches a model that has
            # misread its own situation, and says so plainly enough to correct it.
            return _refusal(
                f"You are {self._caller}. Delegating to yourself does nothing; "
                "either do the work or say what you are missing.",
                available=sorted(self._available),
            )

        if self._available and target not in self._available:
            return _refusal(
                f"There is no agent named '{args.target_agent}'.",
                available=sorted(self._available),
            )

        if self._budget.exhausted:
            # A budget message, not an error: the caller can still finish with
            # what it has, and telling it why is what makes that possible.
            log_event(logger, "delegation.budget_exhausted", caller=self._caller)
            return _refusal(
                f"The delegation budget for this run is used up "
                f"({self._budget.limit}). Answer with what you have, and state "
                "what is missing.",
                available=sorted(self._available),
            )

        self._budget.spent += 1
        task = AgentTask(
            task_id=f"d{uuid.uuid4().hex[:8]}",
            source_agent=self._caller,
            target_agent=target,
            instruction=args.instruction,
            context=list(args.context),
        )
        log_event(
            logger,
            "delegation.dispatched",
            task_id=task.task_id,
            source=task.source_agent,
            target=task.target_agent,
            depth=self._depth,
            spent=self._budget.spent,
        )

        agent = self._factory(target, depth=self._depth + 1)
        result = await agent.run(_prompt_from(task))

        log_event(logger, "delegation.returned", task_id=task.task_id, target=task.target_agent)
        return {
            "task_id": task.task_id,
            "agent": target,
            "answer": result.response.answer,
            "reasoning": result.response.reasoning,
            "caveats": list(result.response.caveats),
            # So the caller's model knows this is another agent's claim rather
            # than something it established itself.
            "note": f"This answer came from the {target} agent, not from you.",
        }


def _refusal(message: str, *, available: list[str]) -> dict[str, Any]:
    """A refusal the model can act on, rather than an exception it cannot."""
    return {"delegated": False, "reason": message, "available_agents": available}


def _prompt_from(task: AgentTask) -> str:
    """Render the structured task as the instruction the delegate receives.

    The contract is the `AgentTask`; this is only its presentation. Validation
    has already happened against the model, which is the point of §10 — the
    failure mode is a rejected field, not a misread sentence.
    """
    if not task.context:
        return task.instruction
    context = "\n".join(f"- {item}" for item in task.context)
    return f"{task.instruction}\n\nContext established by the {task.source_agent} agent:\n{context}"
