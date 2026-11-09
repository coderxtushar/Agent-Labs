"""Application configuration.

All configuration is sourced from environment variables (optionally via a local
``.env`` file).  Secrets are never written to the database or event log; see
:mod:`agentbench.trajectory.redaction`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_prefix="AGENTBENCH_",
        extra="ignore",
    )

    # --- core -------------------------------------------------------------
    env: str = "local"
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"

    #: SQLAlchemy URL.  SQLite is the zero-infrastructure default; PostgreSQL is
    #: supported by installing the ``postgres`` extra and setting this variable.
    database_url: str = Field(
        default_factory=lambda: f"sqlite:///{REPO_ROOT / 'data' / 'agentbench.db'}"
    )

    #: Root directory for run artifacts (patches, logs, exports).
    data_dir: Path = Field(default_factory=lambda: REPO_ROOT / "data")

    #: Directory containing bundled benchmark repositories and task files.
    benchmarks_dir: Path = Field(default_factory=lambda: REPO_ROOT / "benchmarks")

    # --- api --------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    #: When set, every mutating API request must present
    #: ``Authorization: Bearer <token>``.  Required for any non-loopback bind.
    api_token: str | None = None

    #: Refuse to start when bound to a non-loopback address without a token.
    allow_insecure_bind: bool = False

    # --- execution --------------------------------------------------------
    sandbox_provider: str = "docker"  # "docker" | "local_unsafe"

    #: Explicit opt-in required to use the host-local (unsandboxed) provider.
    allow_local_unsafe_sandbox: bool = False

    docker_binary: str = "docker"
    default_sandbox_image: str = "python:3.12-slim"

    #: Hard ceilings applied on top of per-task limits.  A task can request
    #: less, never more.
    max_agent_timeout_s: int = 3600
    max_setup_timeout_s: int = 1800
    max_test_timeout_s: int = 1800
    max_memory_mb: int = 4096
    max_cpus: float = 4.0
    max_concurrent_runs: int = 2

    #: Number of automatic retries for *infrastructure* errors.  Agent failures
    #: are never retried automatically.
    infra_retry_limit: int = 2

    #: Lease duration for a worker claim on a run.  A run whose lease expires is
    #: reclaimable by another worker (crash recovery).
    run_lease_seconds: int = 900

    worker_poll_interval_s: float = 1.0

    #: Start the in-process worker alongside the API server.  Set to false when
    #: running ``agentbench worker`` as a separate process.
    worker_in_api_process: bool = True

    # --- retention --------------------------------------------------------
    event_payload_max_chars: int = 20_000
    log_tail_max_chars: int = 200_000
    #: 0 disables automatic pruning.
    event_retention_days: int = 0

    # --- model providers (used by API-based adapters) ----------------------
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    @field_validator("log_format")
    @classmethod
    def _check_log_format(cls, v: str) -> str:
        if v not in {"json", "console"}:
            raise ValueError("log_format must be 'json' or 'console'")
        return v

    @field_validator("sandbox_provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        if v not in {"docker", "local_unsafe"}:
            raise ValueError("sandbox_provider must be 'docker' or 'local_unsafe'")
        return v

    # --- derived ----------------------------------------------------------
    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    def secret_values(self) -> list[str]:
        """Concrete secret strings that must be redacted from logs and events."""
        candidates = [self.openai_api_key, self.anthropic_api_key, self.api_token]
        return [c for c in candidates if c and len(c) >= 8]

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.artifacts_dir, self.exports_dir, self.workspaces_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Used by tests that mutate the environment."""
    get_settings.cache_clear()
