"""Agent input and output contracts.

`AgentResponse` is what the model must produce. It is deliberately more than a
string: asking for assumptions and confidence separately makes the model's
uncertainty inspectable instead of buried in prose, and gives V1.0's evaluation
harness fields to score.

"Grounded" at V0.1 means: states its assumptions and admits uncertainty. It does
not yet mean "cites retrieved sources" — that arrives at V0.5, and claiming it
now would be the kind of overstatement docs/22-resume-evidence.md forbids.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from amos.llm.base import LLMCallRecord
from amos.tools.base import ToolOutcome


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AgentResponse(BaseModel):
    """The structured answer the model is required to produce."""

    answer: str = Field(description="Direct answer to the goal.")
    reasoning: str = Field(description="How the answer was reached, briefly.")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Anything assumed because the goal did not specify it.",
    )
    confidence: Confidence = Field(description="Confidence in the answer.")
    caveats: list[str] = Field(
        default_factory=list,
        description="What could make this answer wrong, or what it does not cover.",
    )


class GoalRequest(BaseModel):
    """What a client submits."""

    goal: str = Field(min_length=1, max_length=8000)


class AgentResult(BaseModel):
    """The full envelope: the answer plus how it was produced.

    This is the V0.3 seam. `llm_calls` become rows in the `llm_calls` table and
    the envelope itself becomes a `steps` row (docs/05-data-model.md). Shaping it
    correctly now makes V0.3 a persistence change rather than a redesign.
    """

    request_id: str
    response: AgentResponse
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    tool_outcomes: list[ToolOutcome] = Field(default_factory=list)
    repair_count: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
