"""The repair loop is V0.1's central mechanism — these are its tests.

Every test here scripts the provider, so the behaviour under test is the agent's
reaction to bad output, not the model's tendency to produce it.
"""

from __future__ import annotations

import json

import pytest

from amos.agents.agent import GroundedAgent
from amos.agents.schemas import Confidence
from amos.errors import OutputValidationError, ProviderTimeoutError
from amos.llm.fake import AlwaysFailsProvider, FakeProvider
from tests.conftest import valid_response_json


async def test_valid_first_response_makes_one_call(valid_json: str) -> None:
    provider = FakeProvider([valid_json])
    result = await GroundedAgent(provider).run("What is the answer?")

    assert result.response.answer == "42"
    assert result.repair_count == 0
    assert provider.call_count == 1, "a valid response must not trigger a repair"
    assert len(result.llm_calls) == 1


async def test_malformed_json_then_valid_repairs_once(valid_json: str) -> None:
    provider = FakeProvider(["this is not json at all", valid_json])
    result = await GroundedAgent(provider, max_repair_attempts=2).run("goal")

    assert result.repair_count == 1
    assert provider.call_count == 2
    assert result.response.answer == "42"
    # Both attempts are recorded: the failure is evidence, not noise.
    assert len(result.llm_calls) == 2
    assert result.llm_calls[0].error is not None
    assert result.llm_calls[1].error is None


async def test_schema_violation_then_valid_repairs(valid_json: str) -> None:
    """Well-formed JSON that violates the schema must still trigger repair."""
    wrong_shape = json.dumps({"answer": "x", "confidence": "not-a-valid-level"})
    provider = FakeProvider([wrong_shape, valid_json])
    result = await GroundedAgent(provider, max_repair_attempts=2).run("goal")

    assert result.repair_count == 1
    assert result.response.confidence is Confidence.HIGH


async def test_repair_prompt_includes_the_actual_error(valid_json: str) -> None:
    """A repair that doesn't tell the model what was wrong is just a retry."""
    provider = FakeProvider(["not json", valid_json])
    await GroundedAgent(provider).run("goal")

    repair_request = provider.calls[1]
    assert repair_request.system_instruction is not None
    assert "could not be parsed" in repair_request.system_instruction


async def test_exhausted_repairs_raise_typed_error() -> None:
    provider = FakeProvider(["garbage"])
    agent = GroundedAgent(provider, max_repair_attempts=2)

    with pytest.raises(OutputValidationError) as exc:
        await agent.run("goal")

    assert exc.value.attempts == 3, "1 initial attempt + 2 repairs"
    assert provider.call_count == 3
    assert exc.value.last_error


async def test_zero_repair_attempts_fails_immediately() -> None:
    provider = FakeProvider(["garbage"])
    with pytest.raises(OutputValidationError):
        await GroundedAgent(provider, max_repair_attempts=0).run("goal")
    assert provider.call_count == 1


async def test_empty_response_is_treated_as_invalid(valid_json: str) -> None:
    """An empty response is a failure, not an empty answer."""
    provider = FakeProvider(["", valid_json])
    result = await GroundedAgent(provider).run("goal")
    assert result.repair_count == 1


async def test_provider_error_is_not_swallowed_by_repair_loop() -> None:
    """A transport failure is not a validation failure — it must propagate.

    Retrying a timeout here would hide it and burn the repair budget on a
    problem repair cannot fix.
    """
    provider = AlwaysFailsProvider(ProviderTimeoutError("timed out"))
    with pytest.raises(ProviderTimeoutError):
        await GroundedAgent(provider).run("goal")
    assert provider.call_count == 1


async def test_tokens_accumulate_across_repairs(valid_json: str) -> None:
    """A repair costs real tokens; the total must include the wasted attempt."""
    provider = FakeProvider(["bad output here", valid_json])
    result = await GroundedAgent(provider).run("goal")

    assert result.total_tokens == sum(c.prompt_tokens + c.output_tokens for c in result.llm_calls)
    assert result.total_tokens > 0


async def test_request_id_is_present_on_result(valid_json: str) -> None:
    result = await GroundedAgent(FakeProvider([valid_json])).run("goal")
    assert result.request_id
    assert len(result.request_id) == 16


async def test_low_confidence_answer_is_accepted(valid_json: str) -> None:
    """Low confidence is a valid outcome, not a failure to repair."""
    low = valid_response_json(confidence="low", caveats=["Insufficient information."])
    result = await GroundedAgent(FakeProvider([low])).run("goal")

    assert result.response.confidence is Confidence.LOW
    assert result.repair_count == 0
