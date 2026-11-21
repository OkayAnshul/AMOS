"""The fallback chain (ADR-021): order, deadline, and the history rule."""

from __future__ import annotations

import pytest

from amos.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from amos.llm.base import LLMRequest, LLMResponse, Turn
from amos.llm.fallback import FallbackProvider
from amos.observability import call_scope
from amos.tools.base import ToolCall


class Member:
    def __init__(self, name: str, model: str, outcome: object, *, own_history: bool) -> None:
        self.name = name
        self.model = model
        self.requires_own_history = own_history
        self.outcome = outcome
        self.calls = 0
        self.timeouts: list[float] = []

    async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        self.calls += 1
        self.timeouts.append(timeout)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return LLMResponse(text=str(self.outcome), model=self.model, provider=self.name)


def gemini(outcome: object, model: str = "flash-lite") -> Member:
    return Member("gemini", model, outcome, own_history=True)


def groq(outcome: object) -> Member:
    return Member("groq", "gpt-oss-120b", outcome, own_history=False)


async def test_the_first_member_answers_when_it_can() -> None:
    first, last = gemini("from gemini"), groq("from groq")
    response = await FallbackProvider([first, last]).complete(LLMRequest(prompt="x"), timeout=30)
    assert response.text == "from gemini" and last.calls == 0


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailableError("503"),
        ProviderRateLimitError("429", details={"quota_scope": "day"}),
    ],
)
async def test_a_busy_or_exhausted_member_hands_on_to_the_next(error: Exception) -> None:
    chain = [gemini(error), gemini(error, model="gemma"), groq("from groq")]
    response = await FallbackProvider(chain).complete(LLMRequest(prompt="x"), timeout=30)
    assert response.text == "from groq"
    assert [m.calls for m in chain] == [1, 1, 1]


async def test_an_auth_error_is_not_handed_on() -> None:
    """A rejected key is a configuration fault to fix, not to hide behind another provider."""
    with pytest.raises(ProviderAuthError):
        await FallbackProvider([gemini(ProviderAuthError("401")), groq("x")]).complete(
            LLMRequest(prompt="x"), timeout=30
        )


async def test_when_every_member_fails_the_last_error_is_raised() -> None:
    with pytest.raises(ProviderUnavailableError):
        await FallbackProvider(
            [gemini(ProviderUnavailableError("a")), groq(ProviderUnavailableError("b"))]
        ).complete(LLMRequest(prompt="x"), timeout=30)


def history_with_a_tool_call_by(model: str) -> LLMRequest:
    return LLMRequest(
        history=[
            Turn(role="user", text="x"),
            Turn(
                role="model",
                tool_calls=[ToolCall(id="c", name="calculator", arguments={})],
                model=model,
            ),
        ]
    )


async def test_a_gemini_model_is_skipped_for_a_conversation_another_model_called_tools_in() -> None:
    """Gemini 3 rejects function calls in its history without its own signature."""
    other_gemini = gemini("should not be asked", model="gemma")
    chain = [gemini(ProviderUnavailableError("503")), other_gemini, groq("from groq")]
    response = await FallbackProvider(chain).complete(
        history_with_a_tool_call_by("flash-lite"), timeout=30
    )
    assert response.text == "from groq"
    assert other_gemini.calls == 0


async def test_once_groq_has_called_tools_no_gemini_member_is_asked() -> None:
    primary = gemini("must not be asked")
    response = await FallbackProvider([primary, groq("from groq")]).complete(
        history_with_a_tool_call_by("gpt-oss-120b"), timeout=30
    )
    assert response.text == "from groq" and primary.calls == 0


async def test_the_writer_tries_its_preferred_provider_first() -> None:
    primary, writer_choice = gemini("from gemini"), groq("from groq")
    chain = FallbackProvider([primary, writer_choice], prefer={"writer": "groq"})
    with call_scope(agent="writer"):
        response = await chain.complete(LLMRequest(prompt="compose"), timeout=30)
    assert response.text == "from groq" and primary.calls == 0
    assert (await chain.complete(LLMRequest(prompt="x"), timeout=30)).text == "from gemini"


async def test_the_deadline_is_shared_across_the_chain() -> None:
    """ADR-009's bounds hold: the next member gets only the time that is left."""
    now = [0.0]

    class Slow(Member):
        async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
            self.timeouts.append(timeout)
            now[0] += 20
            raise ProviderUnavailableError("503")

    slow = Slow("gemini", "flash-lite", None, own_history=True)
    last = groq("ok")
    await FallbackProvider([slow, last], clock=lambda: now[0]).complete(
        LLMRequest(prompt="x"), timeout=30
    )
    assert slow.timeouts == [30] and last.timeouts == [pytest.approx(10)]


async def test_no_member_is_tried_with_too_little_time_left() -> None:
    now = [0.0]

    class Slow(Member):
        async def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
            now[0] += 28.5
            raise ProviderUnavailableError("503")

    last = groq("never")
    with pytest.raises(ProviderUnavailableError):
        await FallbackProvider(
            [Slow("gemini", "m", None, own_history=True), last], clock=lambda: now[0]
        ).complete(LLMRequest(prompt="x"), timeout=30)
    assert last.calls == 0


async def test_the_planner_prefers_by_purpose() -> None:
    primary, strong = gemini("from gemini"), groq("from groq")
    chain = FallbackProvider([primary, strong], prefer={"planner": "groq"})
    with call_scope(purpose="planner", agent=None):
        assert (await chain.complete(LLMRequest(prompt="plan"), timeout=30)).text == "from groq"


async def test_a_model_that_cannot_fill_the_schema_hands_on() -> None:
    from amos.errors import ProviderGenerationError

    chain = FallbackProvider([groq(ProviderGenerationError("400")), gemini("from gemini")])
    assert (await chain.complete(LLMRequest(prompt="x"), timeout=30)).text == "from gemini"
