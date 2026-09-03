"""The LLM provider seam.

`LLMProvider` is a Protocol, not an ABC, because nothing here needs a shared base
class — providers share a shape, not behaviour. Structural typing means a test
double satisfies the interface without importing or inheriting from anything.

This seam is not speculative generality: the test suite needs a fake provider
regardless (tests must never touch the network), so the abstraction is paid for
by V0.1's own tests. Multi-provider support at V0.2+ is a side effect.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class LLMRequest(BaseModel):
    """A provider-agnostic completion request."""

    prompt: str
    system_instruction: str | None = None
    response_schema: type[BaseModel] | None = None
    temperature: float = 0.2

    model_config = {"arbitrary_types_allowed": True}


class LLMResponse(BaseModel):
    """A provider-agnostic completion response.

    `parsed` is populated only when the provider enforced a schema AND produced
    something conforming. It is deliberately optional: a truncated response
    (MAX_TOKENS mid-JSON) yields text but no parsed object, and the agent's
    repair loop exists precisely for that case.
    """

    text: str
    parsed: BaseModel | None = None
    model: str
    provider: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


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
