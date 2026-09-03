"""Config must fail loudly and early, never silently."""

from __future__ import annotations

import pytest

from amos.config import Settings
from amos.errors import ConfigurationError


def test_require_api_key_raises_when_missing() -> None:
    settings = Settings(gemini_api_key="")
    with pytest.raises(ConfigurationError) as exc:
        settings.require_api_key()
    # The error must tell the user what to actually do about it.
    assert "aistudio.google.com" in str(exc.value)


def test_require_api_key_returns_key_when_present() -> None:
    assert Settings(gemini_api_key="abc").require_api_key() == "abc"


def test_env_prefix_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMOS_LLM_MODEL", "some-other-model")
    assert Settings(_env_file=None).llm_model == "some-other-model"


@pytest.mark.parametrize("bad_timeout", [0, -1, 1000])
def test_timeout_bounds_are_enforced(bad_timeout: float) -> None:
    """N-4: every external call is bounded. A zero or absurd timeout is a bug."""
    with pytest.raises(ValueError):
        Settings(llm_timeout_seconds=bad_timeout)


def test_log_level_is_normalised() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"
