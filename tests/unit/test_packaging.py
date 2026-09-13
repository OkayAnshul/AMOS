"""Every third-party import in `src/` must be a declared *runtime* dependency.

This exists because `httpx` was declared under `[project.optional-dependencies]
dev` with the comment "required by fastapi.testclient" — while
`amos/tools/builtin/http_get.py` imports it at module level. Everyone installs
`-e ".[dev]"`, so nothing ever noticed: `pip install .` produced a package whose
`http_get` tool raised ImportError on the first call.

A test naming httpx specifically would have caught that one bug. This one catches
the class, which is the point — the next dependency to drift will be a different
one.

The import-name → distribution-name mapping comes from
`importlib.metadata.packages_distributions()` rather than a hand-written table,
because a hand-written table is one more thing that goes stale silently.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

#: Import names that are first-party, so no distribution backs them.
FIRST_PARTY = {"amos"}


def _normalise(name: str) -> str:
    """PEP 503 normalisation — `Foo_Bar` and `foo-bar` are the same project."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_runtime_dependencies() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    declared = set()
    for spec in data["project"]["dependencies"]:
        # "sqlalchemy[asyncio]>=2.0.36" -> "sqlalchemy"
        name = re.split(r"[\[<>=!~;\s]", spec, maxsplit=1)[0]
        declared.add(_normalise(name))
    return declared


def _top_level_imports() -> dict[str, set[Path]]:
    """Top-level module name -> the files importing it.

    Relative imports are skipped: `from .base import Tool` has no distribution.
    """
    found: dict[str, set[Path]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level or node.module is None:
                    continue
                names = [node.module]
            else:
                continue
            for name in names:
                found.setdefault(name.split(".")[0], set()).add(path)
    return found


def test_every_src_import_is_a_declared_runtime_dependency() -> None:
    declared = _declared_runtime_dependencies()
    distributions = packages_distributions()

    undeclared: list[str] = []
    for module, files in sorted(_top_level_imports().items()):
        if module in FIRST_PARTY or module in sys.stdlib_module_names:
            continue
        providers = {_normalise(d) for d in distributions.get(module, [])}
        if not providers:
            # Not installed, so it cannot be checked — but it is also not stdlib
            # and not ours, which is itself a packaging problem.
            undeclared.append(f"{module} (no installed distribution provides it)")
            continue
        if not providers & declared:
            where = ", ".join(sorted(str(f.relative_to(SRC)) for f in files))
            undeclared.append(f"{module} (provided by {sorted(providers)}, imported in {where})")

    assert not undeclared, (
        "These imports in src/ are not covered by [project.dependencies] in "
        "pyproject.toml, so `pip install .` would produce a broken package:\n  "
        + "\n  ".join(undeclared)
    )
