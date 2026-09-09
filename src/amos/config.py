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
    embedding_model: str = Field(default="gemini-embedding-001")
    embedding_dimensions: int = Field(
        default=1536,
        ge=128,
        le=2000,
        description=(
            "Must stay <=2000: pgvector's HNSW index limit for the `vector` type. "
            "Truncated embeddings are re-normalised (ADR-008)."
        ),
    )
    otlp_endpoint: str = Field(
        default="",
        description=(
            "OTLP HTTP traces endpoint, e.g. http://localhost:4318/v1/traces. "
            "Empty disables tracing entirely (every span becomes a no-op)."
        ),
    )
    otel_service_name: str = Field(default="amos")
    trace_content: bool = Field(
        default=False,
        description=(
            "Record goal text in span attributes. OFF by default: a goal is user "
            "content and spans are shipped, stored and searchable."
        ),
    )

    worker_poll_interval: float = Field(
        default=2.0, gt=0, le=60, description="Seconds between polls when the queue is empty."
    )
    worker_max_attempts: int = Field(
        default=3, ge=1, le=10, description="Attempts before a run is given up as poison."
    )
    worker_visibility_timeout: int = Field(
        default=600,
        ge=10,
        description="Seconds before a RUNNING run is presumed abandoned and reclaimed.",
    )
    async_enabled: bool = Field(
        default=False,
        description=(
            "Return 202 and queue the run for a worker, instead of executing it "
            "inside the request. Requires a worker process."
        ),
    )

    multi_agent_enabled: bool = Field(
        default=True,
        description="V0.7 specialised agents. Adds a routing call per task.",
    )
    critic_enabled: bool = Field(
        default=True,
        description="V0.7 critic review. Adds a call per answer; costly on a 20/day quota.",
    )
    max_revisions: int = Field(
        default=1, ge=0, le=3, description="Bound on the critic/producer argument."
    )

    memory_enabled: bool = Field(
        default=True,
        description="V0.6 semantic and episodic memory tools. Needs a database.",
    )
    memory_min_score: float = Field(default=0.30, ge=0.0, le=1.0)

    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_min_score: float = Field(default=0.30, ge=0.0, le=1.0)

    task_max_attempts: int = Field(
        default=3, ge=1, le=10, description="Attempts per task before permanent failure."
    )
    planning_enabled: bool = Field(
        default=True,
        description=(
            "V0.4 orchestration. Disable to fall back to the single-shot V0.2 agent — "
            "cheaper on a 20-request/day quota."
        ),
    )

    database_url: str = Field(
        default="",
        description="postgresql+asyncpg://... Required from V0.3.",
    )
    db_echo: bool = Field(default=False, description="Log every SQL statement.")

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

    def require_database_url(self) -> str:
        """Return the database URL, or fail loudly at startup."""
        if not self.database_url:
            raise ConfigurationError(
                "AMOS_DATABASE_URL is not set. Start the database with "
                "`podman-compose up -d` (or `docker compose up -d`), then copy "
                ".env.example to .env — the default URL matches compose.yaml."
            )
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings singleton. Cached so the env is read once per process."""
    return Settings()
