from __future__ import annotations

import json

import pytest

from amos.agents.schemas import AgentResponse, Confidence
from amos.config import Settings


def valid_response_json(**overrides: object) -> str:
    """A schema-valid AgentResponse as JSON text."""
    payload: dict[str, object] = {
        "answer": "42",
        "reasoning": "Computed directly.",
        "assumptions": ["The question was about the canonical answer."],
        "confidence": "high",
        "caveats": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture
def valid_json() -> str:
    return valid_response_json()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        gemini_api_key="test-key-not-real",
        llm_model="fake-model",
        llm_timeout_seconds=5.0,
        llm_max_repair_attempts=2,
        env="test",
        log_level="WARNING",
    )


@pytest.fixture
def sample_response() -> AgentResponse:
    return AgentResponse(
        answer="42",
        reasoning="Computed directly.",
        assumptions=[],
        confidence=Confidence.HIGH,
        caveats=[],
    )
