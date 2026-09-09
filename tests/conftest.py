from __future__ import annotations

import json
from collections.abc import Iterator

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


@pytest.fixture(autouse=True)
def _isolate_request_id() -> Iterator[None]:
    """Reset the request-id contextvar between tests.

    It is a module-level ContextVar, so a test that sets one leaks it into every
    test that runs afterwards in the same process. That surfaced as a telemetry
    test setting "abc123def456" and an unrelated agent test then asserting a
    16-character generated id — a failure in a file that had not changed.

    Autouse, because remembering to clean up global state per test is exactly the
    discipline that fails silently.
    """
    from amos.observability import _request_id

    token = _request_id.set(None)
    try:
        yield
    finally:
        _request_id.reset(token)
