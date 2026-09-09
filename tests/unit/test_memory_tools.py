"""Memory tool contracts, without a database.

Exercises argument validation and the exact-before-similar ordering by faking the
stores, so the tool's own behaviour is under test rather than the SQL.
"""

from __future__ import annotations

import pytest

from amos.memory.tools import RecallFactsTool, RememberFactTool
from amos.tools.base import ToolCall, ToolStatus


class _FakeSemantic:
    def __init__(self, exact: object | None = None, similar: list[object] | None = None):
        self.exact = exact
        self.similar = similar or []
        self.remembered: list[tuple[str, str]] = []

    async def recall_exact(self, subject: str) -> object | None:
        return self.exact

    async def recall_similar(self, query: str, **_: object) -> list[object]:
        return self.similar

    async def remember(self, subject: str, content: str, **_: object) -> object:
        self.remembered.append((subject, content))

        class _F:
            pass

        f = _F()
        f.subject = subject  # type: ignore[attr-defined]
        f.content = content  # type: ignore[attr-defined]
        return f


@pytest.mark.parametrize(
    "bad",
    [
        {"subject": "", "content": "x"},
        {"subject": "s"},
        {"subject": "s", "content": "x" * 2001},
        {"subject": "s" * 201, "content": "x"},
    ],
)
async def test_remember_rejects_invalid_arguments(bad: dict[str, object]) -> None:
    tool = RememberFactTool(None, None)
    outcome = await tool.execute(ToolCall(id="m", name="remember_fact", arguments=bad))
    assert outcome.status is ToolStatus.INVALID_ARGS


async def test_recall_rejects_an_empty_query() -> None:
    tool = RecallFactsTool(None, None)
    outcome = await tool.execute(ToolCall(id="m", name="recall_facts", arguments={"query": ""}))
    assert outcome.status is ToolStatus.INVALID_ARGS


async def test_recall_prefers_an_exact_match_over_similarity(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When the caller knows the key, a ranking is the wrong answer — it can
    return a similar fact about someone else."""

    class _Fact:
        subject = "user_name"
        content = "Anshul"

    fake = _FakeSemantic(exact=_Fact(), similar=[])
    tool = RecallFactsTool(object(), object())

    monkeypatch.setattr("amos.memory.tools.SemanticMemory", lambda *_a, **_k: fake)
    monkeypatch.setattr(
        "amos.database.engine.session_scope",
        lambda _f: _NullScope(),
    )

    outcome = await tool.execute(
        ToolCall(id="m", name="recall_facts", arguments={"query": "user_name"})
    )
    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert outcome.output["match"] == "exact"


async def test_recall_instructs_against_invention_when_nothing_is_stored(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    fake = _FakeSemantic(exact=None, similar=[])
    monkeypatch.setattr("amos.memory.tools.SemanticMemory", lambda *_a, **_k: fake)
    monkeypatch.setattr("amos.database.engine.session_scope", lambda _f: _NullScope())

    tool = RecallFactsTool(object(), object())
    outcome = await tool.execute(
        ToolCall(id="m", name="recall_facts", arguments={"query": "anything"})
    )
    assert outcome.output is not None
    assert outcome.output["found"] == 0
    assert "Do not invent" in outcome.output["instruction"]


class _NullScope:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_: object) -> None:
        return None


def test_remember_tool_is_not_granted_write_permission() -> None:
    """Writing memory persists a model decision. It stays under a bounded,
    single-row permission until the approval workflow exists."""
    from amos.tools.base import Permission

    assert RememberFactTool.permission is Permission.READ_LOCAL
