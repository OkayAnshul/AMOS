"""The fake is test infrastructure — if it lies, every test above it lies."""

from __future__ import annotations

import pytest

from amos.agents.schemas import AgentResponse
from amos.errors import ProviderError
from amos.llm.base import LLMProvider, LLMRequest
from amos.llm.fake import AlwaysFailsProvider, FakeProvider


def test_fake_satisfies_the_provider_protocol() -> None:
    """Structural typing: the fake must be substitutable without inheritance."""
    assert isinstance(FakeProvider(["x"]), LLMProvider)
    assert isinstance(AlwaysFailsProvider(), LLMProvider)


def test_empty_script_is_rejected() -> None:
    with pytest.raises(ValueError):
        FakeProvider([])


async def test_parses_when_output_matches_schema(valid_json: str) -> None:
    provider = FakeProvider([valid_json])
    response = await provider.complete(
        LLMRequest(prompt="x", response_schema=AgentResponse), timeout=1
    )
    assert isinstance(response.parsed, AgentResponse)


async def test_parsed_is_none_when_output_is_malformed() -> None:
    """This is the behaviour the repair-loop tests depend on."""
    provider = FakeProvider(["not json"])
    response = await provider.complete(
        LLMRequest(prompt="x", response_schema=AgentResponse), timeout=1
    )
    assert response.parsed is None
    assert response.text == "not json"


async def test_script_advances_then_repeats_last(valid_json: str) -> None:
    provider = FakeProvider(["first", valid_json])
    request = LLMRequest(prompt="x", response_schema=AgentResponse)

    assert (await provider.complete(request, timeout=1)).text == "first"
    assert (await provider.complete(request, timeout=1)).text == valid_json
    assert (await provider.complete(request, timeout=1)).text == valid_json


async def test_scripted_exception_is_raised() -> None:
    provider = FakeProvider([ProviderError("boom")])
    with pytest.raises(ProviderError):
        await provider.complete(LLMRequest(prompt="x"), timeout=1)
