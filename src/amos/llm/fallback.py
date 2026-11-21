"""An ordered chain of models: when one cannot answer, the next does (ADR-021).

Gemini primary, then a Gemini fallback model, then Groq. A call moves on when a
member raises a 503, a rate limit, or a timeout — **inside the caller's one
deadline**, shared across the chain, so ADR-009's bounds still hold.

**The rule the chain keeps.** Gemini 3 rejects function calls in its history that
carry no signature it made. So once a tool loop holds a tool call made by model M,
a Gemini member that is not M is skipped for that call; Groq accepts any history
and can always take over. Decided from the model recorded on each turn.

**Preferences.** An agent can prefer a member — the writer prefers Groq's
stronger model (it has no tools, so no history question arises). A preference
reorders the chain for that agent's calls; it never removes a member.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from amos.database.recorder import record_event
from amos.errors import ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError
from amos.llm.base import LLMProvider, LLMRequest, LLMResponse
from amos.observability import get_call_context, log_event

logger = logging.getLogger(__name__)

#: Failures the next member may succeed at. Auth errors, budget and bad requests are not.
FALL_THROUGH = (ProviderUnavailableError, ProviderRateLimitError, ProviderTimeoutError)

#: Below this much remaining deadline, no further member is tried.
MIN_SECONDS_FOR_NEXT = 3.0


def model_of(provider: Any) -> str:
    """The model a (possibly wrapped) provider calls."""
    current = provider
    for _ in range(6):
        model = getattr(current, "model", None)
        if isinstance(model, str):
            return model
        legacy = getattr(current, "_model", None)
        if isinstance(legacy, str):
            return legacy
        current = getattr(current, "inner", None)
        if current is None:
            break
    return "unknown"


def needs_own_history(provider: Any) -> bool:
    """Whether a member rejects tool calls another model made (Gemini does)."""
    current = provider
    for _ in range(6):
        flag = getattr(current, "requires_own_history", None)
        if isinstance(flag, bool):
            return flag
        current = getattr(current, "inner", None)
        if current is None:
            break
    return True


class FallbackProvider:
    def __init__(
        self,
        members: Sequence[LLMProvider],
        *,
        prefer: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not members:
            raise ValueError("a fallback chain needs at least one member")
        self._members = list(members)
        self._prefer = dict(prefer or {})
        self._clock = clock
        self.name = members[0].name

    @property
    def inner(self) -> LLMProvider:
        return self._members[0]

    @property
    def members(self) -> list[LLMProvider]:
        return list(self._members)

    def _order(self) -> list[LLMProvider]:
        agent = get_call_context().agent
        preferred = self._prefer.get(agent or "")
        if not preferred:
            return list(self._members)
        first = [m for m in self._members if m.name == preferred]
        return first + [m for m in self._members if m.name != preferred]

    def _eligible(self, member: LLMProvider, request: LLMRequest) -> bool:
        if not needs_own_history(member):
            return True
        tool_models = {t.model for t in request.history if t.role == "model" and t.tool_calls}
        return not tool_models or tool_models == {model_of(member)}

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        deadline = self._clock() + timeout
        last: Exception | None = None
        tried: list[str] = []
        for member in self._order():
            if not self._eligible(member, request):
                continue
            remaining = deadline - self._clock()
            if tried and remaining < MIN_SECONDS_FOR_NEXT:
                break
            try:
                response = await member.complete(request, timeout=remaining)
            except FALL_THROUGH as exc:
                last = exc
                tried.append(f"{member.name}/{model_of(member)}")
                log_event(logger, "llm.fallback", failed=tried[-1], error=type(exc).__name__)
                continue
            if tried:
                await record_event(
                    "provider.fallback",
                    failed=tried,
                    answered=f"{member.name}/{model_of(member)}",
                    error=type(last).__name__ if last else None,
                )
            return response
        if last is None:
            raise ProviderUnavailableError(
                "No model in the chain could continue this conversation.",
                details={"tried": tried},
            )
        raise last
