"""Configuration from the environment (12-factor).

Settings are read once at startup and validated immediately. A missing API key
raises here, at import/startup, rather than surfacing as a confusing failure on
the first request an hour later.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from amos.errors import ConfigurationError


class Settings(BaseSettings):
    """All AMOS configuration. Env prefix: AMOS_"""

    model_config = SettingsConfigDict(
        env_prefix="AMOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = Field(default="", description="Gemini API key")
    llm_model: str = Field(
        default="gemini-3.5-flash-lite",
        description=(
            "Free-tier quota is PER MODEL and daily (20/day for gemini-3.5-flash). "
            "Defaulting to lite preserves the flash quota for demos."
        ),
    )
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_repair_attempts: int = Field(default=2, ge=0, le=5)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    agent_max_iterations: int = Field(
        default=5, ge=1, le=15, description="Hard cap on tool-calling rounds per goal."
    )
    tool_sandbox_root: str = Field(default=".", description="Directory read_file is confined to.")

    env: str = Field(default="development")
    log_level: str = Field(default="INFO")

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    def require_api_key(self) -> str:
        """Return the API key, or fail loudly.

        Kept separate from validation so tests can build Settings without a key.
        """
        if not self.gemini_api_key:
            raise ConfigurationError(
                "AMOS_GEMINI_API_KEY is not set. Copy .env.example to .env and add a key "
                "from https://aistudio.google.com/apikey"
            )
        return self.gemini_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings singleton. Cached so the env is read once per process."""
    return Settings()
