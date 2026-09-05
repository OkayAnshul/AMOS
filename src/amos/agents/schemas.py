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
    run_id: str | None = Field(
        default=None, description="Set when persistence is enabled; use with GET /v1/runs/{id}."
    )
    response: AgentResponse
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    tool_outcomes: list[ToolOutcome] = Field(default_factory=list)
    tasks: list[TaskRecord] = Field(default_factory=list)
    outcome: str = Field(
        default="COMPLETED",
        description="COMPLETED, PARTIALLY_COMPLETED or FAILED (V0.4).",
    )
    repair_count: int = 0
    total_tokens: int = 0
    latency_ms: int = 0


class TaskRecord(BaseModel):
    """A task's outcome, as the caller sees it.

    Carried on AgentResult so the orchestrator satisfies the same interface as
    the single-shot agents — which is what lets RunService persist any of them
    without knowing which produced the result.
    """

    plan_ref: str
    description: str
    state: str
    depends_on: list[str] = Field(default_factory=list)
    attempt_count: int = 0
    position: int = 0
    answer: str | None = None
    error: str | None = None


class TraceTask(BaseModel):
    plan_ref: str
    description: str
    state: str
    depends_on: list[str] = Field(default_factory=list)
    attempt_count: int = 0
    error: str | None = None


class TraceLLMCall(BaseModel):
    provider: str
    model: str
    prompt_tokens: int
    output_tokens: int
    latency_ms: int
    repair_attempt: int
    error: str | None = None


class TraceToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    status: str
    output: dict[str, object] | None = None
    error: str | None = None
    latency_ms: int


class TraceStep(BaseModel):
    attempt: int
    status: str
    agent_name: str
    error: dict[str, object] | None = None


class RunTrace(BaseModel):
    """The answer to "what exactly happened on this request?".

    Assembled from stored rows only — nothing here comes from memory, which is
    what makes it answerable for a run that happened weeks ago.
    """

    run_id: str
    goal: str
    status: str
    request_id: str | None = None
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None
    total_tokens: int
    latency_ms: int
    created_at: str
    completed_at: str | None = None
    tasks: list[TraceTask] = Field(default_factory=list)
    steps: list[TraceStep] = Field(default_factory=list)
    llm_calls: list[TraceLLMCall] = Field(default_factory=list)
    tool_calls: list[TraceToolCall] = Field(default_factory=list)
