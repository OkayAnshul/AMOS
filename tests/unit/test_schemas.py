"""The output contract is the boundary between model text and typed data."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from amos.agents.schemas import AgentResponse, Confidence, GoalRequest


def test_valid_response_parses(valid_json: str) -> None:
    response = AgentResponse.model_validate_json(valid_json)
    assert response.confidence is Confidence.HIGH
    assert response.answer == "42"


def test_missing_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentResponse.model_validate({"answer": "x"})  # no reasoning, no confidence


def test_invalid_confidence_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentResponse.model_validate(
            {"answer": "x", "reasoning": "y", "confidence": "extremely-sure"}
        )


def test_optional_lists_default_to_empty() -> None:
    response = AgentResponse.model_validate({"answer": "x", "reasoning": "y", "confidence": "low"})
    assert response.assumptions == []
    assert response.caveats == []


def test_empty_goal_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoalRequest(goal="")


def test_oversized_goal_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoalRequest(goal="x" * 8001)
