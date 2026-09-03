"""Typed error hierarchy.

Every failure crossing a layer boundary is one of these, never a bare Exception.
The API layer maps them to status codes in one place (`api/app.py`), so adding an
error type never means hunting for `except` blocks.
"""

from __future__ import annotations


class AmosError(Exception):
    """Base for every AMOS error."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(AmosError):
    """Invalid or missing configuration. Raised at startup, never at request time."""


class ProviderError(AmosError):
    """The LLM provider failed."""


class ProviderTimeoutError(ProviderError):
    """The provider did not respond within the configured timeout."""


class ProviderAuthError(ProviderError):
    """The provider rejected our credentials."""


class ProviderRateLimitError(ProviderError):
    """The provider rate-limited us. Free tier is ~15 RPM."""


class OutputValidationError(AmosError):
    """Model output failed validation after every repair attempt was exhausted.

    This is the terminal state of the repair loop: we asked, it produced something
    unusable, we asked again with the error, and it still did not comply. Callers
    get a typed failure rather than an unvalidated object.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        last_error: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.attempts = attempts
        self.last_error = last_error


class ToolError(AmosError):
    """Base for tool failures."""


class ToolNotFoundError(ToolError):
    """The model asked for a tool that does not exist.

    A hallucinated tool name is expected behaviour, not an exceptional one: the
    model is told what exists and sometimes invents something else. It must be a
    recoverable, typed failure the model can be told about — never a crash.
    """


class ToolValidationError(ToolError):
    """Tool arguments failed schema validation, before any execution."""


class ToolTimeoutError(ToolError):
    """A tool exceeded its declared timeout."""


class ToolPermissionError(ToolError):
    """A tool was called by an agent whose allowlist does not include it."""


class ToolLoopExhaustedError(AmosError):
    """The agent hit its tool-call iteration cap without producing an answer.

    An unbounded tool loop is a runaway cost and a hung request. The cap is a
    hard guarantee enforced by code, not a suggestion in a prompt.
    """

    def __init__(self, message: str, *, iterations: int, tool_calls: int) -> None:
        super().__init__(message, details={"iterations": iterations, "tool_calls": tool_calls})
        self.iterations = iterations
        self.tool_calls = tool_calls
