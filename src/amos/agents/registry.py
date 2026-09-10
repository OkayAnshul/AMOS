"""Agent specialisation.

## What makes an agent "specialised"

Three prompts over the same tools is **not** a multi-agent system — it is one
agent with a mood ring, and an interviewer will ask exactly that question.

The property that makes these agents genuinely different is a **tool allowlist
enforced in code**. Each agent is built with a `ToolRegistry` containing only its
permitted tools, so a Researcher asking for `calculator` gets `NOT_FOUND` — not
because it was told not to, but because the tool does not exist inside its
registry.

That gives specialisation the same standing as every other guarantee in AMOS: it
is a property of the code, not of the prompt.

## Why an allowlist per agent rather than one shared registry

Least privilege. The Critic's job is to judge whether an answer is supported by
its sources; giving it `http_get` would let it go and find *new* sources, which
is a different job and one that makes its verdict unfalsifiable. The Analyst
should not be able to fetch the web mid-calculation.

Narrow capability is also what makes routing meaningful: if every agent could do
everything, choosing between them would be arbitrary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from amos.errors import AmosError
from amos.tools.registry import ToolRegistry


class UnknownAgentError(AmosError):
    """A task was routed to an agent that does not exist."""


@dataclass(frozen=True)
class AgentSpec:
    """The identity and capability of one specialised agent."""

    name: str
    purpose: str
    #: Tool names this agent may use. Enforced by construction, not by prompt.
    tools: frozenset[str]
    system_instruction: str
    max_iterations: int = 5
    #: Shown to the router. Written for a classifier, not for a human.
    routing_hint: str = ""

    def registry_from(self, available: ToolRegistry) -> ToolRegistry:
        """A registry containing only this agent's permitted tools.

        Tools it is not allowed are simply absent, so an attempt to use one
        returns NOT_FOUND through the existing machinery rather than needing a
        new enforcement path.
        """
        return ToolRegistry([tool for tool in available if tool.name in self.tools])


RESEARCHER = AgentSpec(
    name="researcher",
    purpose="Find information from documents, the web, and remembered facts.",
    # `recall_past_runs` lives here, not on the analyst. The first assignment put
    # it with the analyst on the reasoning that past outcomes inform judgement —
    # but recalling a past run is a *lookup*, the same shape as searching
    # documents or recalling a fact. The router disagreed with the original
    # labelling and was right; see engineering/experiments-log.md.
    # `remember_fact` lives here too, and its absence was a real bug: with
    # multi-agent enabled it was in NO agent's allowlist, so storing a fact was
    # structurally impossible — every specialist's registry filtered it out.
    #
    # It belongs with the researcher because that agent already owns every memory
    # *read*. Splitting reads and writes across agents would mean "what did I
    # tell you, and also remember this" could not be done by one agent, and a
    # dedicated memory agent would add a third routable specialist for one tool.
    tools=frozenset(
        {
            "search_knowledge",
            "http_get",
            "read_file",
            "recall_facts",
            "recall_past_runs",
            "remember_fact",
        }
    ),
    routing_hint="finding, looking up, reading, searching, what does X say",
    system_instruction="""You are AMOS's researcher. You find information; you do not analyse it.

- Prefer search_knowledge for anything about this project.
- Report what the sources say, with citations. Do not add your own conclusions.
- If the sources do not answer the question, say so. Do not fill the gap from memory.
- You have no calculator. If a number needs computing, report the inputs and say so.
- You also own MEMORY. If the user states a durable fact about themselves or
  their project, call remember_fact. Never say you have noted something without
  calling it.""",
)

ANALYST = AgentSpec(
    name="analyst",
    purpose="Compute, compare and reason over information already gathered.",
    tools=frozenset({"calculator"}),
    routing_hint="calculate, compare, how much, which is larger, analyse, work out",
    system_instruction="""You are AMOS's analyst. You reason over information you are given.

- Use the calculator for every arithmetic step. Do not compute in your head.
- You cannot search, fetch or recall anything. If information is missing, say
  precisely what you need rather than guessing at it.
- Show the steps that led to your conclusion.""",
)

CRITIC = AgentSpec(
    name="critic",
    purpose="Judge whether an answer is supported by its evidence.",
    #: Deliberately empty. A critic that can fetch new sources is doing research,
    #: and its verdict becomes unfalsifiable — it could always find something to
    #: justify whatever it already concluded.
    tools=frozenset(),
    routing_hint="(not routed to directly; invoked to validate another agent's output)",
    system_instruction="""You are AMOS's critic. You judge answers; you do not produce them.

Check only these things:
- Is every factual claim supported by the evidence provided?
- Does the answer actually address the goal that was asked?
- Are stated assumptions and caveats honest about what is missing?

You have NO tools. Judge only what you were given. If the evidence is thin, say the
answer is unsupported — do not go looking for reasons to accept it.

Be specific. "Needs improvement" is useless; "claim X is not in any cited source" is not.""",
)

DEFAULT_SPECS: tuple[AgentSpec, ...] = (RESEARCHER, ANALYST, CRITIC)

#: Agents the router may choose. The critic validates output rather than doing
#: work, so routing a task to it would be a category error.
ROUTABLE = tuple(spec for spec in DEFAULT_SPECS if spec.name != "critic")


@dataclass
class AgentRegistry:
    """The set of specialised agents available to the orchestrator."""

    specs: tuple[AgentSpec, ...] = field(default_factory=lambda: DEFAULT_SPECS)

    def get(self, name: str) -> AgentSpec:
        for spec in self.specs:
            if spec.name == name:
                return spec
        raise UnknownAgentError(
            f"No agent named '{name}'. Available: {', '.join(self.names)}",
            details={"requested": name, "available": list(self.names)},
        )

    def has(self, name: str) -> bool:
        return any(spec.name == name for spec in self.specs)

    @property
    def names(self) -> list[str]:
        return [spec.name for spec in self.specs]

    @property
    def routable(self) -> list[AgentSpec]:
        return [spec for spec in self.specs if spec.name != "critic"]
