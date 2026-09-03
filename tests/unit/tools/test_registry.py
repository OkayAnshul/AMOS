from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from amos.errors import ToolNotFoundError, ToolPermissionError
from amos.tools.base import Permission, Tool
from amos.tools.builtin import CalculatorTool, HttpGetTool, ReadFileTool
from amos.tools.registry import ToolRegistry


class _Args(BaseModel):
    value: str


class _WriteTool(Tool):
    name: ClassVar[str] = "dangerous"
    description: ClassVar[str] = "Would write things."
    input_schema: ClassVar[type[BaseModel]] = _Args
    permission: ClassVar[Permission] = Permission.WRITE

    async def _run(self, args: _Args) -> dict[str, Any]:
        return {}


def test_register_and_get() -> None:
    registry = ToolRegistry([CalculatorTool()])
    assert registry.get("calculator").name == "calculator"
    assert len(registry) == 1


def test_unknown_tool_raises_with_available_names() -> None:
    """The error must tell the model what does exist, or it cannot recover."""
    registry = ToolRegistry([CalculatorTool()])
    with pytest.raises(ToolNotFoundError) as exc:
        registry.get("nonexistent")
    assert "calculator" in exc.value.message


def test_duplicate_registration_is_rejected() -> None:
    registry = ToolRegistry([CalculatorTool()])
    with pytest.raises(ValueError):
        registry.register(CalculatorTool())


def test_write_permission_is_refused_in_code() -> None:
    """Not merely discouraged in a doc — the registry refuses it."""
    with pytest.raises(ToolPermissionError) as exc:
        ToolRegistry([_WriteTool()])
    assert "approval" in exc.value.message


def test_specs_are_sorted_for_prompt_stability(tmp_path: Path) -> None:
    registry = ToolRegistry([ReadFileTool(tmp_path), CalculatorTool(), HttpGetTool()])
    assert [spec.name for spec in registry.specs()] == [
        "calculator",
        "http_get",
        "read_file",
    ]


def test_spec_is_generated_from_the_pydantic_schema() -> None:
    """One source of truth: what the model is told is what the code validates."""
    spec = CalculatorTool.spec()
    assert spec.name == "calculator"
    assert "expression" in spec.parameters["properties"]
    assert spec.parameters["required"] == ["expression"]
    assert "title" not in spec.parameters
