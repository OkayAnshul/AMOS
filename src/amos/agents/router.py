"""Routing: which specialised agent should do a task.

The router is a small LLM call whose output is **validated against the registry**.
A hallucinated agent name is not an error — it is expected occasionally — so it
falls back to a default rather than failing the task.

Falling back rather than raising is the right trade here: routing to the wrong
agent produces a worse answer, while failing the task produces none. A wrong
route is recoverable; a dead task is not.

Routing accuracy is **measured** (`evaluate_routing`), because "specialised
agents" is otherwise an unmeasured claim — three prompts and a hopeful story.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from amos.agents.messages import RoutingDecision
from amos.agents.registry import AgentRegistry
from amos.llm.base import LLMCallRecord, LLMProvider, LLMRequest
from amos.observability import log_event

logger = logging.getLogger(__name__)

ROUTER_INSTRUCTION = """You assign a task to the agent best suited to it.

Agents:
{agents}

Rules:
- Choose exactly one agent name from the list, spelled exactly as shown.
- Choose on what the task REQUIRES, not on its topic. A task needing arithmetic
  goes to the analyst even if the subject is documentation.
- If a task needs both finding and computing, choose the agent for the FIRST
  thing that must happen.
"""


class Router:
    """Assigns tasks to agents."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: AgentRegistry,
        *,
        default_agent: str = "researcher",
        timeout: float = 30.0,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._default = default_agent
        self._timeout = timeout

    async def route(
        self, instruction: str, calls: list[LLMCallRecord] | None = None
    ) -> RoutingDecision:
        agents = "\n".join(
            f"- {spec.name}: {spec.purpose} (typical: {spec.routing_hint})"
            for spec in self._registry.routable
        )
        response = await self._provider.complete(
            LLMRequest(
                prompt=f"Task: {instruction}",
                system_instruction=ROUTER_INSTRUCTION.format(agents=agents),
                response_schema=RoutingDecision,
                temperature=0.0,  # routing should be stable, not creative
            ),
            timeout=self._timeout,
        )
        if calls is not None:
            calls.append(
                LLMCallRecord(
                    provider=response.provider,
                    model=response.model,
                    prompt_tokens=response.prompt_tokens,
                    output_tokens=response.output_tokens,
                    latency_ms=response.latency_ms,
                )
            )

        decision = (
            response.parsed
            if isinstance(response.parsed, RoutingDecision)
            else RoutingDecision(agent=self._default, reason="router output invalid")
        )

        # Validate against the registry. The critic is excluded because routing
        # work to a validator is a category error, not a routing preference.
        if not self._registry.has(decision.agent) or decision.agent == "critic":
            log_event(
                logger,
                "router.invalid_agent",
                requested=decision.agent,
                fell_back_to=self._default,
            )
            return RoutingDecision(
                agent=self._default,
                reason=f"'{decision.agent}' is not a routable agent; used the default",
            )

        log_event(logger, "router.decided", agent=decision.agent)
        return decision


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingCase:
    """A task with the agent that should handle it."""

    instruction: str
    expected_agent: str
    note: str = ""


@dataclass
class RoutingResult:
    total: int
    correct: int
    mistakes: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def summary(self) -> str:
        return f"routing accuracy = {self.accuracy:.1%} ({self.correct}/{self.total})"


async def evaluate_routing(router: Router, cases: list[RoutingCase]) -> RoutingResult:
    """Score the router against labelled tasks.

    Mistakes record what was chosen instead, because "80% accurate" is a
    scoreboard while "these four went to the researcher and should have gone to
    the analyst" is something you can act on.
    """
    result = RoutingResult(total=len(cases), correct=0)
    for case in cases:
        decision = await router.route(case.instruction)
        if decision.agent == case.expected_agent:
            result.correct += 1
        else:
            result.mistakes.append((case.instruction, case.expected_agent, decision.agent))
    return result


#: Labelled routing cases. Phrased as a planner would emit them, and deliberately
#: including tasks whose *topic* suggests one agent while their *requirement*
#: suggests another — a router that only pattern-matches subject matter fails these.
ROUTING_CASES: list[RoutingCase] = [
    RoutingCase("Find what the documentation says about pgvector", "researcher"),
    RoutingCase("Read the file docs/13-security.md and summarise it", "researcher"),
    RoutingCase("Look up the retry policy in the project docs", "researcher"),
    RoutingCase("Recall what the user said their preferred language is", "researcher"),
    RoutingCase("Calculate 17 percent of 2340", "analyst"),
    RoutingCase("Compare 397.8 and 345 and say which is larger", "analyst"),
    RoutingCase("Work out the total token cost given 467 and 522 tokens", "analyst"),
    RoutingCase(
        "Calculate how many chunks 28 documents produce at 11 chunks each",
        "analyst",
        note="Topic is documentation, requirement is arithmetic — routes on requirement.",
    ),
    RoutingCase(
        "Find the daily request quota stated in the technology baseline",
        "researcher",
        note="Topic is a number, requirement is lookup.",
    ),
    RoutingCase(
        "Check whether previous runs solved a similar goal",
        "researcher",
        note=(
            "Relabelled after measurement. Originally 'analyst', because "
            "recall_past_runs was assigned there. The router chose researcher and "
            "was right: recalling a past run is a lookup. The TOOL moved; the "
            "label followed the fix, not the other way round."
        ),
    ),
]
