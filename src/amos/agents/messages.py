"""Structured messages between agents.

Agents exchange typed objects, never free-form prose (project brief §10).

A natural-language handoff — "hey, can you check the flight prices for Japan" —
is unparseable, unvalidatable and untestable. When the receiving agent
misunderstands, there is nothing to point at: no field was wrong, because there
were no fields.

Structured contracts make the failure mode *validation* rather than
*misinterpretation*, which is the difference between a bug you can catch and a
bug you have to notice.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"


class AgentTask(BaseModel):
    """Work handed to a specialised agent."""

    task_id: str
    source_agent: str
    target_agent: str
    instruction: str = Field(min_length=1, max_length=2000)
    context: list[str] = Field(
        default_factory=list,
        description="Results of upstream tasks this one may use.",
    )


class CriticReport(BaseModel):
    """The critic's judgement of another agent's output.

    `unsupported_claims` is the field that carries the work. A verdict alone
    ("revise") gives the producing agent nothing to act on; naming the claims
    that lack support does.
    """

    verdict: Verdict
    reasoning: str = Field(min_length=1, max_length=2000)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=10)
    missing_from_answer: list[str] = Field(default_factory=list, max_length=10)

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPT


class RoutingDecision(BaseModel):
    """Which agent should do a task, and why."""

    agent: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)
