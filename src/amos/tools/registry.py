"""Tool registry.

Holds the tools an agent may use and produces the declarations sent to the
model. Registration is explicit: tools are added at startup, not discovered by
scanning the filesystem. Implicit discovery would mean the set of capabilities
an agent has depends on which files happen to be importable — an unpleasant
property for something that decides what the system may touch.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from amos.errors import ToolNotFoundError, ToolPermissionError
from amos.tools.base import Permission, Tool, ToolSpec


class ToolRegistry:
    """A named collection of tools."""

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        if tool.permission in (Permission.WRITE, Permission.DESTRUCTIVE):
            # Refused in code, not merely discouraged in a document. These
            # permissions require the approval workflow, which is not built.
            raise ToolPermissionError(
                f"Tool '{tool.name}' declares permission '{tool.permission}', which requires "
                "the human-approval workflow (docs/13-security.md). Not implemented."
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Look up a tool, or raise with the list of ones that do exist."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(
                f"No tool named '{name}'. Available: {', '.join(sorted(self._tools)) or 'none'}",
                details={"requested": name, "available": sorted(self._tools)},
            ) from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def specs(self) -> list[ToolSpec]:
        """Declarations for the model, in stable order.

        Sorted so the prompt is deterministic — an unstable tool order changes
        the prompt between runs and quietly defeats provider-side caching.
        """
        return [tool.spec() for _, tool in sorted(self._tools.items())]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)
