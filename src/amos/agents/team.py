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
from amos.agents.registry import WEB_INSTRUCTION, AgentRegistry, AgentSpec
from amos.agents.router import Router
from amos.agents.schemas import AgentResponse, AgentResult, Confidence
from amos.agents.sourcing import SourceLedger, cited, unseen
from amos.agents.tool_agent import ToolUsingAgent
from amos.database.recorder import record_event
from amos.llm.base import LLMCallRecord, LLMProvider
from amos.observability import call_scope, log_event
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
        max_iterations: int | None = None,
        critic_enabled: bool = True,
        routing_enabled: bool = True,
        delegation_enabled: bool = True,
        max_delegation_depth: int = DEFAULT_MAX_DEPTH,
        delegation_budget: int = DEFAULT_BUDGET,
        sources: SourceLedger | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._agents = agents or AgentRegistry()
        self._timeout = timeout
        self._temperature = temperature
        self._max_revisions = max_revisions
        # `AMOS_AGENT_MAX_ITERATIONS` when given; otherwise each spec's own bound.
        # Before V1.6 the setting never reached the team, so with multi-agent on
        # (the default) it did nothing.
        self._max_iterations = max_iterations
        self._critic_enabled = critic_enabled
        self._routing_enabled = routing_enabled
        self._delegation_enabled = delegation_enabled
        self._max_delegation_depth = max_delegation_depth
        self._delegation_budget = delegation_budget
        # What this run has seen, for checking a writer's citations (ADR-020).
        # None without a database: nothing to check against, so no check.
        self._sources = sources
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
        if "web_search" in registry.names:
            instruction = f"{instruction}\n{WEB_INSTRUCTION}"

        if spec.delegates and self._delegation_enabled and depth < self._max_delegation_depth:
            shared = budget if budget is not None else DelegationBudget(self._delegation_budget)
            others = frozenset(
                other.name
                for other in self._agents.routable
                if other.name != spec.name and other.delegates
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
            max_iterations=self._max_iterations or spec.max_iterations,
            temperature=self._temperature,
            system_instruction=instruction,
        )

    async def run(self, goal: str) -> AgentResult:
        calls: list[LLMCallRecord] = []

        with call_scope(purpose="router", agent=None):
            spec, reason = await self._choose(goal, calls)
        # One lane per agent on the page comes from this event (ADR-015).
        with call_scope(agent=spec.name):
            await record_event("task.routed", agent=spec.name, reason=reason)

        # One budget for the whole task, created here so what delegates spend
        # can be added back to this result rather than vanishing (V1.7).
        budget = DelegationBudget(self._delegation_budget)
        agent = self.agent_for(spec, budget=budget)
        with call_scope(purpose="agent", agent=spec.name):
            result = await agent.run(goal)
        calls.extend(result.llm_calls)

        answer = result.response
        report: CriticReport | None = None

        if self._critic_enabled:
            answer, report, review_calls = await self._review_and_revise(
                goal, agent, answer, result, spec.name, composes=not spec.tools
            )
            calls.extend(review_calls)

        if not spec.tools:
            # A tool-less agent composes from material it was given; every source
            # it cites must be one this run actually saw (ADR-020). Code checks,
            # not a model.
            answer = await self._check_citations(answer)

        calls.extend(budget.llm_calls)
        return AgentResult(
            request_id=result.request_id,
            response=answer,
            llm_calls=calls,
            tool_outcomes=[*result.tool_outcomes, *budget.tool_outcomes],
            tasks=result.tasks,
            outcome=result.outcome,
            agent_name=spec.name,
            critic_verdict=report.verdict.value if report else None,
            total_tokens=sum(c.prompt_tokens + c.output_tokens for c in calls),
            latency_ms=result.latency_ms,
        )

    async def _choose(self, goal: str, calls: list[LLMCallRecord]) -> tuple[AgentSpec, str]:
        """Pick a specialist, and say why. Skipped when there is nothing to
        choose between."""
        routable = self._agents.routable
        if not self._routing_enabled or len(routable) < 2:
            return routable[0], "only one agent could do this"
        decision = await self._router.route(goal, calls)
        return self._agents.get(decision.agent), decision.reason

    async def _review_and_revise(
        self,
        goal: str,
        agent: ToolUsingAgent,
        answer: AgentResponse,
        result: AgentResult,
        agent_name: str = "agent",
        *,
        composes: bool = False,
    ) -> tuple[AgentResponse, CriticReport, list[LLMCallRecord]]:
        """Review, and allow a bounded number of revisions.

        The loop is capped in code. Critic and producer can disagree forever, and
        an unbounded argument between two models spends a day's quota in minutes.
        """
        calls: list[LLMCallRecord] = []
        evidence = _evidence_from(result)
        if composes and not evidence:
            # The writer has no tool calls to show; what it may use is the material
            # in its instruction — the earlier tasks' results — so that is what the
            # critic judges it against. With no evidence at all, every sentence
            # of a deliverable would read as unsupported.
            evidence = [f"Material the writer was given:\n{goal[:COMPOSER_EVIDENCE_CHARS]}"]
        report = await self._review(goal, answer, evidence, calls, revision=0)

        for attempt in range(self._max_revisions):
            if report.accepted:
                break
            log_event(
                logger,
                "critic.requested_revision",
                attempt=attempt + 1,
                unsupported=len(report.unsupported_claims),
            )
            with call_scope(purpose="revision", agent=agent_name):
                revised = await agent.run(_revision_prompt(goal, answer, report))
            calls.extend(revised.llm_calls)
            answer = revised.response
            evidence = _evidence_from(revised) or evidence
            report = await self._review(goal, answer, evidence, calls, revision=attempt + 1)

        if not report.accepted:
            # Budget exhausted with objections outstanding. Return the answer WITH
            # the objections rather than discarding it or hiding them.
            log_event(logger, "critic.unresolved", unsupported=len(report.unsupported_claims))
            answer = apply_report(answer, report)

        return answer, report, calls

    async def _check_citations(self, answer: AgentResponse) -> AgentResponse:
        if self._sources is None:
            return answer
        seen = await self._sources.seen()
        cited_sources = cited(answer.answer)
        missing = unseen(answer.answer, seen)
        await record_event(
            "sources.checked",
            cited=len(cited_sources),
            seen=len(seen),
            unseen=missing[:20],
        )
        if missing:
            answer.caveats.append(
                "Cites sources this run never saw: "
                + ", ".join(missing[:8])
                + ". Treat those claims as unverified."
            )
            answer.confidence = Confidence.LOW
            log_event(logger, "sources.unseen", count=len(missing))
        return answer

    async def _review(
        self,
        goal: str,
        answer: AgentResponse,
        evidence: list[str],
        calls: list[LLMCallRecord],
        *,
        revision: int,
    ) -> CriticReport:
        """One critic review, recorded as a verdict event (never stored before V1.7)."""
        with call_scope(purpose="critic", agent="critic"):
            report = await self._critic.review(goal, answer, evidence, calls)
            await record_event(
                "critic.verdict",
                verdict=report.verdict.value,
                reasoning=report.reasoning,
                unsupported_claims=list(report.unsupported_claims),
                revision=revision,
                evidence_items=len(evidence),
            )
        return report


def _delegation_instruction(agents: AgentRegistry, spec: AgentSpec) -> str:
    """What to tell an agent that actually has the `delegate` tool.

    Built from the registry rather than written into each spec, so adding an
    agent does not mean editing every other agent's prompt to mention it.
    """
    others = [other for other in agents.routable if other.name != spec.name and other.delegates]
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
        output = outcome.output
        passages = output.get("passages")
        if isinstance(passages, list):
            evidence.extend(
                f"{p.get('citation', '?')}: {str(p.get('content', ''))[:600]}"
                for p in passages
                if isinstance(p, dict)
            )
        elif isinstance(output.get("text"), str):
            # A web page (V1.9). Before this branch the critic got the first 600
            # characters of the output *dict* — mostly its url and instruction —
            # and flagged facts from the page as unsupported because it had never
            # been shown the page. Found on the first live web run.
            evidence.append(
                f"{output.get('url', outcome.name)}: {output['text'][:PAGE_EVIDENCE_CHARS]}"
            )
        elif isinstance(output.get("results"), list):
            evidence.extend(
                f"{r.get('url')} (search snippet): {r.get('title', '')} — {r.get('snippet', '')}"
                for r in output["results"]
                if isinstance(r, dict)
            )
        else:
            evidence.append(f"{outcome.name}: {str(output)[:600]}")
    return evidence


#: How much of the writer's material the critic sees.
COMPOSER_EVIDENCE_CHARS = 12_000

#: How much of a read page the critic sees. Enough for the facts an answer
#: quotes; bounded because every page read costs critic tokens.
PAGE_EVIDENCE_CHARS = 6000


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
