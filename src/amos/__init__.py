"""AMOS — Autonomous Multi-Agent Operating System.

The version is defined **here**, and `pyproject.toml` reads it via hatchling's
`version = {attr = "amos.__version__"}`.

The first attempt did this the other way round — `importlib.metadata.version()`
reading from the installed distribution — which reports the version recorded at
*install* time. In an editable install that is whatever it was when `pip install
-e` last ran, so bumping pyproject silently changed nothing and `/health` kept
reporting 0.3.0.

One definition, and the build derives from it rather than the other way about.
"""

__version__ = "0.7.0"

__all__ = ["__version__"]
