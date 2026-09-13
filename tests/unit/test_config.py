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


def test_every_setting_is_read_by_something() -> None:
    """A declared setting nobody reads is a promise the system does not keep.

    Three had drifted into exactly that state. The worst was
    `AMOS_ASYNC_ENABLED`: its description promised "return 202 and queue the run
    instead of executing it inside the request", three documents instructed the
    reader to set it, and no code read it — so setting it did nothing and not
    setting it did nothing. `retrieval_top_k` and `memory_min_score` were quieter
    versions of the same thing, made harder to spot by their siblings
    (`retrieval_min_score`) being wired correctly.

    Grep, not import graph: the question is whether a human reading the source
    would find the setting used, which is the same question a reader of the docs
    is implicitly asking.
    """
    import ast
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config_path = root / "src" / "amos" / "config.py"

    tree = ast.parse(config_path.read_text())
    settings_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    fields = [
        node.target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    assert fields, "no settings found — the parser is wrong, not the config"

    sources = [
        path.read_text()
        for path in [*(root / "src").rglob("*.py"), *(root / "tests").rglob("*.py")]
        if path != config_path
    ]

    unread = [
        field
        for field in fields
        if not any(re.search(rf"\b{re.escape(field)}\b", text) for text in sources)
    ]
    assert not unread, (
        f"these settings are declared but never read, so setting them does nothing: {unread}"
    )


def test_every_setting_has_an_env_example_entry() -> None:
    """`.env.example` is how an operator discovers what is configurable.

    It stopped at "--- Database (V0.3) ---" while fourteen settings arrived in
    the six milestones after it, so the file documented a third of the surface
    and silently implied the rest did not exist.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    declared = set(
        re.findall(r"^\s{4}([a-z_]+):\s", (root / "src" / "amos" / "config.py").read_text(), re.M)
    )
    documented = {
        line.split("=")[0].removeprefix("AMOS_").lower()
        for line in (root / ".env.example").read_text().splitlines()
        if line.startswith("AMOS_")
    }
    assert not declared - documented, (
        f"settings with no .env.example entry: {sorted(declared - documented)}"
    )
    assert not documented - declared, (
        f".env.example names settings that do not exist: {sorted(documented - declared)}"
    )
