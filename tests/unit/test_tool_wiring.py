"""What tools does the agent actually get?

This file exists because of a real bug: the V0.6 memory tools were written,
tested and documented, but a patch to `build_registry` silently failed to apply,
so they were never registered. Every unit test passed. The agent was simply
offered four tools instead of seven, and answered "I have noted that" without
having stored anything.

Nothing asserted the registry's contents, so nothing caught it. That is the gap
these tests close: **the wiring is a behaviour, and behaviours need tests.**
"""

from __future__ import annotations

from amos.api.dependencies import build_registry
from amos.config import Settings


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "gemini_api_key": "test-key",
        "database_url": "postgresql+asyncpg://user:pw@localhost/db",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_all_tools_are_registered_when_a_database_is_present() -> None:
    assert build_registry(settings(), object()).names == [
        "calculator",
        "http_get",
        "read_file",
        "recall_facts",
        "recall_past_runs",
        "remember_fact",
        "search_knowledge",
    ]


def test_database_backed_tools_are_absent_without_a_database() -> None:
    """Fewer tools, not tools that fail on every call."""
    assert build_registry(settings(), None).names == [
        "calculator",
        "http_get",
        "read_file",
    ]


def test_memory_can_be_disabled_independently_of_retrieval() -> None:
    names = build_registry(settings(memory_enabled=False), object()).names
    assert "search_knowledge" in names
    assert "remember_fact" not in names


def test_every_tool_declares_a_distinct_name() -> None:
    names = build_registry(settings(), object()).names
    assert len(names) == len(set(names))


def test_no_registered_tool_holds_write_permission() -> None:
    """The registry refuses WRITE/DESTRUCTIVE, but assert it at the wiring level
    too — a tool's permission could be changed without anyone re-reading the
    registry's guard."""
    from amos.tools.base import Permission

    registry = build_registry(settings(), object())
    assert all(
        tool.permission in (Permission.PURE, Permission.READ_LOCAL, Permission.NETWORK_READ)
        for tool in registry
    )


def test_tool_descriptions_are_distinct_enough_to_choose_between() -> None:
    """`search_knowledge` and `recall_facts` were confused by the model in the
    first V0.6 demo: one searches ingested documents, the other recalls facts the
    user stated. Their descriptions must not be paraphrases of each other."""
    registry = build_registry(settings(), object())
    knowledge = registry.get("search_knowledge").description.lower()
    facts = registry.get("recall_facts").description.lower()

    # Each names its own subject...
    assert "ingested documents" in knowledge
    assert "the user told you" in facts

    # ...and each explicitly points at the other, which is what actually stops
    # the model conflating them. Checking for the absence of a word would be
    # brittle: "does not search documentation" legitimately contains "document".
    assert "recall_facts" in knowledge
    assert "search_knowledge" in facts


def test_the_version_is_defined_in_exactly_one_place() -> None:
    """The version was previously a literal in three files and drifted the first
    time only some were updated. It now lives in `amos.__version__`, with the
    build deriving from it — and the health endpoint reading it, not repeating it.
    """
    from pathlib import Path

    from amos import __version__

    app_source = Path("src/amos/api/app.py").read_text()
    assert f'"{__version__}"' not in app_source, "version literal reappeared in app.py"
    assert "__version__" in app_source

    pyproject = Path("pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in pyproject
    assert f'version = "{__version__}"' not in pyproject


def test_the_version_matches_the_latest_git_tag() -> None:
    """`__version__` was left at 0.7.0 through V0.8, V0.9 and V1.0.

    Nothing caught it: the earlier test only asserted the version was defined in
    one place, not that the value was *right*. `/health` cheerfully reported
    0.7.0 from a v1.0 build, which is the kind of wrong that survives because it
    is never fatal.
    """
    import subprocess

    from amos import __version__

    tags = subprocess.run(
        ["git", "tag", "-l", "v*"], capture_output=True, text=True, check=False
    ).stdout.split()
    if not tags:
        return  # a shallow clone or a fresh repo has no tags to compare against

    def key(tag: str) -> tuple[int, ...]:
        return tuple(int(part) for part in tag.lstrip("v").split("."))

    latest = max(tags, key=key)
    assert __version__.startswith(latest.lstrip("v")), (
        f"__version__ is {__version__} but the latest tag is {latest}"
    )


def test_every_registered_tool_is_reachable_by_at_least_one_agent() -> None:
    """A tool nobody can call is a tool that does not exist.

    This closes a real bug: `remember_fact` was registered globally and appeared
    in `build_registry()`, but was in **no agent's allowlist** — so with
    multi-agent enabled (the default) every specialist's registry filtered it
    out and storing a fact was structurally impossible.

    The existing wiring test passed throughout, because it checks the *global*
    registry. Nothing checked that the per-agent filtering left every tool
    reachable by somebody. Registration and reachability are different
    properties.
    """
    from amos.agents.registry import AgentRegistry

    registered = set(build_registry(settings(), object()).names)
    reachable: set[str] = set()
    for spec in AgentRegistry().specs:
        reachable |= spec.tools

    unreachable = registered - reachable
    assert not unreachable, f"registered but no agent can call them: {sorted(unreachable)}"


def test_each_agents_registry_contains_exactly_its_allowlist() -> None:
    """The filtering itself, asserted against the real tool set."""
    from amos.agents.registry import AgentRegistry

    tools = build_registry(settings(), object())
    for spec in AgentRegistry().specs:
        filtered = set(spec.registry_from(tools).names)
        assert filtered == spec.tools & set(tools.names), spec.name
