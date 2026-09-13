"""The agent team: routing, specialised execution, and criticism.

This is the piece that makes "multi-agent" honest. Three things have to be true,
and each is enforced rather than asserted:

1. **Agents differ in capability, not just wording.** Each is built with a
   registry containing only its allowlisted tools, so a Researcher asking for
   `calculator` gets NOT_FOUND from the existing machinery.
2. **They communicate in structured messages** (`AgentTask`, `CriticReport`),
   never free-form prose.
3. **A critic gates the output**, and the revise loop is bounded in code.

What it costs: routing adds one call per task, and criticism adds one per review.
On a 20-request/day quota that is not free, which is why `AMOS_CRITIC_ENABLED`
exists and why routing is skipped when only one agent could possibly apply.
"""

from __future__ import annotations

import logging

from amos.agents.critic import Critic, apply_report
from amos.agents.delegation import (
    DEFAULT_BUDGET,
    DEFAULT_MAX_DEPTH,
    DelegateTool,
    DelegationBudget,
)
from amos.agents.messages import CriticReport
from amos.agents.registry import AgentRegistry, AgentSpec
from amos.agents.router import Router
from amos.agents.schemas import AgentResponse, AgentResult
from amos.agents.tool_agent import ToolUsingAgent
from amos.llm.base import LLMCallRecord, LLMProvider
from amos.observability import log_event
from amos.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentTeam:
    """Runs one task through the right specialist, then reviews the result.

    Satisfies `run(goal) -> AgentResult`, the same interface as every earlier
    agent — so the executor, the orchestrator and `RunService` are unchanged.
    Four milestones of that interface holding is why V0.7 adds a layer rather
    than rewriting one.
    """

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        *,
        agents: AgentRegistry | None = None,
        timeout: float = 60.0,
        temperature: float = 0.2,
        max_revisions: int = 1,
        critic_enabled: bool = True,
        routing_enabled: bool = True,
        delegation_enabled: bool = True,
        max_delegation_depth: int = DEFAULT_MAX_DEPTH,
        delegation_budget: int = DEFAULT_BUDGET,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._agents = agents or AgentRegistry()
        self._timeout = timeout
        self._temperature = temperature
        self._max_revisions = max_revisions
        self._critic_enabled = critic_enabled
        self._routing_enabled = routing_enabled
        self._delegation_enabled = delegation_enabled
        self._max_delegation_depth = max_delegation_depth
        self._delegation_budget = delegation_budget
        self._router = Router(provider, self._agents, timeout=timeout)
        self._critic = Critic(provider, timeout=timeout)

    @property
    def tool_names(self) -> list[str]:
        return self._tools.names

    def agent_for(
        self,
        spec: AgentSpec,
        *,
        depth: int = 0,
        budget: DelegationBudget | None = None,
    ) -> ToolUsingAgent:
        """Build a specialist restricted to its own tools.

        The restriction is structural: the agent is handed a registry that does
        not contain the other tools, so refusing them needs no extra code path.

        Delegation is the same mechanism one level up. A specialist gets the
        `delegate` tool only while `depth < max_delegation_depth`; past that the
        tool is **absent from its registry**, so the bound is not a check the
        model could be talked past (ADR-012).
        """
        registry = spec.registry_from(self._tools)
        instruction = spec.system_instruction

        if self._delegation_enabled and depth < self._max_delegation_depth:
            shared = budget if budget is not None else DelegationBudget(self._delegation_budget)
            others = frozenset(
                other.name for other in self._agents.routable if other.name != spec.name
            )
            if others:
                registry = ToolRegistry(
                    [
                        *registry,
                        DelegateTool(
                            spec.name,
                            lambda name, *, depth: self.agent_for(
                                self._agents.get(name), depth=depth, budget=shared
                            ),
                            shared,
                            depth=depth,
                            available=others,
                        ),
                    ]
                )
                # The instruction is extended only when the tool is actually
                # present. A prompt promising a capability the registry denies
                # produces an agent that repeatedly attempts a tool it cannot
                # have, burning iterations — the rule in
                # docs/07-agent-specification.md, now applied to a tool whose
                # presence varies with depth.
                instruction = f"{instruction}\n{_delegation_instruction(self._agents, spec)}"

        return ToolUsingAgent(
            self._provider,
            registry,
            timeout=self._timeout,
            max_iterations=spec.max_iterations,
            temperature=self._temperature,
            system_instruction=instruction,
        )

    async def run(self, goal: str) -> AgentResult:
        calls: list[LLMCallRecord] = []

        spec = await self._choose(goal, calls)
        agent = self.agent_for(spec)
        result = await agent.run(goal)
        calls.extend(result.llm_calls)

        answer = result.response
        report: CriticReport | None = None

        if self._critic_enabled:
            answer, report, review_calls = await self._review_and_revise(
                goal, agent, answer, result
            )
            calls.extend(review_calls)

        return AgentResult(
            request_id=result.request_id,
            response=answer,
            llm_calls=calls,
            tool_outcomes=result.tool_outcomes,
            tasks=result.tasks,
            outcome=result.outcome,
            agent_name=spec.name,
            critic_verdict=report.verdict.value if report else None,
            total_tokens=sum(c.prompt_tokens + c.output_tokens for c in calls),
            latency_ms=result.latency_ms,
        )

    async def _choose(self, goal: str, calls: list[LLMCallRecord]) -> AgentSpec:
        """Pick a specialist. Skipped when there is nothing to choose between."""
        routable = self._agents.routable
        if not self._routing_enabled or len(routable) < 2:
            return routable[0]
        decision = await self._router.route(goal, calls)
        return self._agents.get(decision.agent)

    async def _review_and_revise(
        self,
        goal: str,
        agent: ToolUsingAgent,
        answer: AgentResponse,
        result: AgentResult,
    ) -> tuple[AgentResponse, CriticReport, list[LLMCallRecord]]:
        """Review, and allow a bounded number of revisions.

        The loop is capped in code. Critic and producer can disagree forever, and
        an unbounded argument between two models spends a day's quota in minutes.
        """
        calls: list[LLMCallRecord] = []
        evidence = _evidence_from(result)
        report = await self._critic.review(goal, answer, evidence, calls)

        for attempt in range(self._max_revisions):
            if report.accepted:
                break
            log_event(
                logger,
                "critic.requested_revision",
                attempt=attempt + 1,
                unsupported=len(report.unsupported_claims),
            )
            revised = await agent.run(_revision_prompt(goal, answer, report))
            calls.extend(revised.llm_calls)
            answer = revised.response
            evidence = _evidence_from(revised) or evidence
            report = await self._critic.review(goal, answer, evidence, calls)

        if not report.accepted:
            # Budget exhausted with objections outstanding. Return the answer WITH
            # the objections rather than discarding it or hiding them.
            log_event(logger, "critic.unresolved", unsupported=len(report.unsupported_claims))
            answer = apply_report(answer, report)

        return answer, report, calls


def _delegation_instruction(agents: AgentRegistry, spec: AgentSpec) -> str:
    """What to tell an agent that actually has the `delegate` tool.

    Built from the registry rather than written into each spec, so adding an
    agent does not mean editing every other agent's prompt to mention it.
    """
    others = [other for other in agents.routable if other.name != spec.name]
    lines = "\n".join(f"  - {other.name}: {other.purpose}" for other in others)
    return (
        "\n- If a step needs a capability you do not have, use `delegate` rather "
        "than guessing at it or giving up. Available specialists:\n"
        f"{lines}\n"
        "  Give a self-contained instruction and include what you have already "
        "established - the other agent cannot see your work. Delegation is "
        "limited, so use it for what you genuinely cannot do."
    )


def _evidence_from(result: AgentResult) -> list[str]:
    """What the critic is allowed to judge against: only what the agent actually
    retrieved. Giving it anything else would let it validate against sources the
    answer never saw."""
    evidence: list[str] = []
    for outcome in result.tool_outcomes:
        if not outcome.succeeded or not outcome.output:
            continue
        passages = outcome.output.get("passages")
        if isinstance(passages, list):
            evidence.extend(
                f"{p.get('citation', '?')}: {str(p.get('content', ''))[:600]}"
                for p in passages
                if isinstance(p, dict)
            )
        else:
            evidence.append(f"{outcome.name}: {str(outcome.output)[:600]}")
    return evidence


def _revision_prompt(goal: str, answer: AgentResponse, report: CriticReport) -> str:
    parts = [
        f"Original goal: {goal}",
        f"Your previous answer: {answer.answer}",
        f"A reviewer rejected it: {report.reasoning}",
    ]
    if report.unsupported_claims:
        parts.append("Claims not supported by evidence: " + "; ".join(report.unsupported_claims))
    if report.missing_from_answer:
        parts.append("Not addressed: " + "; ".join(report.missing_from_answer))
    parts.append(
        "Revise. Remove or support each unsupported claim. If you cannot support "
        "something, drop it and say so in caveats rather than restating it."
    )
    return "\n\n".join(parts)
