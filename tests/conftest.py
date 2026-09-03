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


def isolated_settings(**overrides: object) -> Settings:
    """Settings that ignore the developer's .env file.

    Without `_env_file=None`, tests inherit whatever is in the local .env — so
    adding AMOS_DATABASE_URL there made the entire integration suite try to open
    real database connections. Tests must not depend on a developer's machine.
    """
    defaults: dict[str, object] = {
        "gemini_api_key": "test-key-not-real",
        "llm_model": "fake-model",
        "llm_timeout_seconds": 5.0,
        "llm_max_repair_attempts": 2,
        "database_url": "",  # no persistence unless a test asks for it
        "env": "test",
        "log_level": "WARNING",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


@pytest.fixture
def test_settings() -> Settings:
    return isolated_settings()


@pytest.fixture
def sample_response() -> AgentResponse:
    return AgentResponse(
        answer="42",
        reasoning="Computed directly.",
        assumptions=[],
        confidence=Confidence.HIGH,
        caveats=[],
    )
