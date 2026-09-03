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
