"""Sandbox escape tests.

The path is model-supplied and therefore hostile. These tests are the security
boundary; if they pass for the wrong reason the tool is a file-disclosure bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amos.tools.base import ToolCall, ToolStatus
from amos.tools.builtin import ReadFileTool


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("# inside the sandbox\n")
    (tmp_path / "secret.env").write_text("KEY=should-not-be-readable\n")
    return tmp_path


@pytest.fixture
def tool(sandbox: Path) -> ReadFileTool:
    return ReadFileTool(sandbox)


def call(path: str) -> ToolCall:
    return ToolCall(id="r1", name="read_file", arguments={"path": path})


async def test_reads_a_file_inside_the_sandbox(tool: ReadFileTool) -> None:
    outcome = await tool.execute(call("docs/note.md"))
    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert "inside the sandbox" in outcome.output["content"]


@pytest.mark.parametrize(
    "escape",
    [
        "../../../../etc/passwd",
        "/etc/passwd",
        "docs/../../../../etc/passwd",
        "..",
        "docs/../../..",
    ],
)
async def test_traversal_is_blocked(tool: ReadFileTool, escape: str) -> None:
    outcome = await tool.execute(call(escape))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert outcome.output is None


async def test_symlink_escape_is_blocked(tool: ReadFileTool, sandbox: Path) -> None:
    """The attack a string-based `..` filter misses entirely.

    The path contains no traversal characters; only resolving it reveals that it
    leaves the sandbox.
    """
    outside = sandbox.parent / "outside.md"
    outside.write_text("# outside\n")
    (sandbox / "docs" / "innocent.md").unlink(missing_ok=True)
    (sandbox / "docs" / "innocent.md").symlink_to(outside)

    outcome = await tool.execute(call("docs/innocent.md"))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "outside" in (outcome.error or "").lower()


async def test_disallowed_extension_is_rejected(tool: ReadFileTool) -> None:
    """secret.env is inside the sandbox — the extension allowlist stops it."""
    outcome = await tool.execute(call("secret.env"))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "not permitted" in (outcome.error or "")


async def test_missing_file_is_a_clean_error(tool: ReadFileTool) -> None:
    outcome = await tool.execute(call("docs/nope.md"))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "No such file" in (outcome.error or "")


async def test_oversized_file_is_refused(tool: ReadFileTool, sandbox: Path) -> None:
    (sandbox / "docs" / "big.md").write_text("x" * 200_000)
    outcome = await tool.execute(call("docs/big.md"))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "limit" in (outcome.error or "")


async def test_non_utf8_file_is_refused(tool: ReadFileTool, sandbox: Path) -> None:
    (sandbox / "docs" / "binary.md").write_bytes(b"\xff\xfe\x00\x01")
    outcome = await tool.execute(call("docs/binary.md"))
    assert outcome.status is ToolStatus.INVALID_ARGS
