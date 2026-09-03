"""The LLM provider seam.

`LLMProvider` is a Protocol, not an ABC, because nothing here needs a shared base
class — providers share a shape, not behaviour. Structural typing means a test
double satisfies the interface without importing or inheriting from anything.

This seam is not speculative generality: the test suite needs a fake provider
regardless (tests must never touch the network), so the abstraction is paid for
by V0.1's own tests. Multi-provider support at V0.2+ is a side effect.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from amos.tools.base import ToolCall, ToolOutcome, ToolSpec


class Turn(BaseModel):
    """One turn of a conversation, provider-agnostic.

    Added at V0.2 for multi-step tool use. Kept separate from the vendor's
    content types so the agent loop never imports a provider SDK.

    `provider_state` is the deliberate exception: an opaque blob that only the
    provider which produced it may interpret. Gemini 3.x requires a
    `thought_signature` on function-call parts to be returned *verbatim* — a
    reconstructed equivalent is rejected with a 400. Some vendor continuation
    state simply cannot be modelled generically, so it is carried as an opaque
    token rather than pretended away. ADR-005 anticipated needing this escape
    hatch; this is it.

    The agent loop never reads this field. Only the provider does.
    """

    role: Literal["user", "model", "tool"]
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_outcomes: list[ToolOutcome] = Field(default_factory=list)
    provider_state: Any = Field(default=None, repr=False, exclude=True)


class LLMRequest(BaseModel):
    """A provider-agnostic completion request.

    `prompt` is the single-turn form from V0.1 and still works unchanged.
    `history`, when non-empty, takes precedence — that is how V0.2's tool loop
    carries a growing conversation without V0.1's agent needing to change.
    """

    prompt: str = ""
    history: list[Turn] = Field(default_factory=list)
    system_instruction: str | None = None
    response_schema: type[BaseModel] | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    temperature: float = 0.2

    model_config = {"arbitrary_types_allowed": True}

    def turns(self) -> list[Turn]:
        """Normalise to a turn list, whichever form the caller used."""
        if self.history:
            return self.history
        return [Turn(role="user", text=self.prompt)]


class LLMResponse(BaseModel):
    """A provider-agnostic completion response.

    `parsed` is populated only when the provider enforced a schema AND produced
    something conforming. It is deliberately optional: a truncated response
    (MAX_TOKENS mid-JSON) yields text but no parsed object, and the agent's
    repair loop exists precisely for that case.
    """

    text: str
    parsed: BaseModel | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str
    provider: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str | None = None
    provider_state: Any = Field(default=None, repr=False, exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMCallRecord(BaseModel):
    """One provider call, recorded.

    This is the V0.3 seam: these fields are the `llm_calls` table columns
    (docs/05-data-model.md). At V0.1 they are logged; at V0.3 the same object is
    persisted. That makes V0.3 a serialisation change rather than a redesign.
    """

    provider: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    repair_attempt: int = Field(default=0, description="0 = first try, 1+ = repair")
    error: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Anything that can turn a request into a response.

    Implementations must raise ProviderTimeoutError / ProviderAuthError /
    ProviderRateLimitError / ProviderError rather than leaking vendor exceptions,
    so callers never import a vendor SDK to handle an error.
    """

    name: str

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        """Run a completion. Must respect `timeout` and raise typed errors."""
        ...
