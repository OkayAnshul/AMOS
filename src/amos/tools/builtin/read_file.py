"""Sandboxed local file reading.

The path is model-supplied, so it is hostile input. Two distinct attacks matter:

1. **Traversal** — `../../../../etc/passwd`. Defeated by resolving the path and
   confirming the result is inside the sandbox root.
2. **Symlink escape** — a symlink *inside* the sandbox pointing outside it. This
   is why the check uses `Path.resolve()` (which follows symlinks) and compares
   the *resolved* path. Validating the string before resolution would pass a
   symlink straight through.

The check is on the resolved path, never on the string. String-level filtering
of `..` is the classic mistake: it misses symlinks, absolute paths, and
encodings entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from amos.errors import ToolValidationError
from amos.tools.base import Permission, Tool

_MAX_BYTES = 100_000
_ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".py", ".json", ".toml", ".yaml", ".yml", ".cfg"})


class ReadFileArgs(BaseModel):
    path: str = Field(
        max_length=500,
        description="Path relative to the project root, e.g. 'docs/00-vision.md'",
    )


class ReadFileTool(Tool):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read a UTF-8 text file from the project directory. Paths are relative to the "
        "project root. Only documentation and source files can be read."
    )
    input_schema: ClassVar[type[BaseModel]] = ReadFileArgs
    permission: ClassVar[Permission] = Permission.READ_LOCAL
    timeout_seconds: ClassVar[float] = 5.0

    def __init__(self, sandbox_root: Path | str) -> None:
        # Resolved once at construction: the boundary must not be re-derived
        # per call from anything the model can influence.
        self._root = Path(sandbox_root).resolve()

    async def _run(self, args: ReadFileArgs) -> dict[str, Any]:
        target = self._resolve_within_sandbox(args.path)

        if target.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ToolValidationError(
                f"Reading '{target.suffix}' files is not permitted. "
                f"Allowed: {', '.join(sorted(_ALLOWED_SUFFIXES))}"
            )
        if not target.is_file():
            raise ToolValidationError(f"No such file: {args.path}")

        size = target.stat().st_size
        if size > _MAX_BYTES:
            raise ToolValidationError(
                f"File is {size} bytes; the limit is {_MAX_BYTES}. Read a smaller file."
            )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolValidationError(f"{args.path} is not valid UTF-8 text") from exc

        return {
            "path": str(target.relative_to(self._root)),
            "bytes": size,
            "content": content,
        }

    def _resolve_within_sandbox(self, raw_path: str) -> Path:
        """Resolve, then verify containment. Order matters."""
        candidate = (self._root / raw_path).resolve()

        # Path.is_relative_to compares resolved paths, so a symlink that escapes
        # the sandbox fails here even though its literal string looked innocent.
        if not candidate.is_relative_to(self._root):
            raise ToolValidationError(
                f"Path '{raw_path}' resolves outside the permitted directory. "
                "Only files inside the project root can be read."
            )
        return candidate
