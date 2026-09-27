"""Application settings (spec sections 4, 27, 28).

All process configuration comes from environment variables with the AIND_
prefix (or .env for local runs). Secrets are never read from this object:
provider credentials live behind the SecretResolver.
"""

from __future__ import annotations

import json
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"
    log_level: str = "INFO"

    # --- storage ---
    database_url: str = (
        "postgresql+psycopg://ainative:change-me@localhost:55432/ainative_control"
    )
    secrets_dir: Path = Path("infra/local-secrets")
    results_dir: Path = Path("runtime/results")

    # --- web ---
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    cookie_secure: bool = False
    cookie_name: str = "ainative_session"
    csrf_cookie_name: str = "ainative_csrf"
    session_ttl_hours: int = 8
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300

    # --- cursors / signatures ---
    cursor_secret: str | None = None

    # --- gateway budgets (spec section 4) ---
    request_max_bytes: int = 64 * 1024
    default_max_rows: int = 1_000
    hard_max_rows: int = 10_000
    hard_max_bytes: int = 10 * 1024 * 1024
    default_timeout_seconds: int = 30
    hard_max_timeout_seconds: int = 120
    max_relations: int = 8
    max_subquery_depth: int = 4

    # --- concurrency / scheduling (spec section 4, 13) ---
    global_concurrency: int = 4
    per_datasource_concurrency: int = 2
    per_user_concurrency: int = 2
    queue_timeout_seconds: int = 60
    worker_poll_seconds: float = 1.0
    heartbeat_seconds: int = 5
    lease_seconds: int = 30
    lost_after_seconds: int = 60
    cancel_poll_seconds: float = 0.5

    # --- retention (spec section 4) ---
    result_ttl_days: int = 7
    event_retention_days: int = 7
    audit_retention_days: int = 90
    idempotency_ttl_hours: int = 24

    # --- metadata cache (spec section 10) ---
    metadata_snapshot_ttl_seconds: int = 900
    metadata_dir: Path = Path("metadata")

    # --- DataHub (spec sections 10, 27) ---
    datahub_enabled: bool = False
    datahub_gms_url: str = "http://datahub-gms:8080"
    datahub_frontend_url: str = "http://127.0.0.1:9002"
    datahub_token: str | None = None
    datahub_timeout_seconds: float = 15.0
    datahub_metadata_cache_ttl_seconds: int = 300
    datahub_search_candidate_limit: int = 200
    datahub_visibility_poll_seconds: int = 45
    datahub_version: str = "v1.7.0.1"
    ingestion_recipe_dir: Path = Path("metadata/recipes")
    ingestion_work_dir: Path = Path("runtime/ingestion")

    # --- governed LLM adapter (spec section 20) ---
    llm_provider: str = "fake"
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = ""
    llm_api_key_file: Path | None = None
    llm_timeout_seconds: float = 30.0
    # "json_object" is the portable mode (DeepSeek and most OpenAI-compatible
    # servers reject "json_schema"; the schema then travels in the prompt and the
    # response is validated against it locally). Set "json_schema" only for
    # providers that implement strict structured outputs.
    llm_response_format: str = "json_object"
    agent_max_input_tokens: int = 60_000
    agent_max_output_tokens: int = 12_000
    agent_max_tool_calls: int = 20
    agent_max_queries: int = 12
    agent_max_sql_repairs: int = 2
    agent_max_wall_seconds: int = 180

    # --- sensitive-data policy (spec section 12.3) ---
    # Column names matching this regex cannot be registered as queryable
    # datasets unless they live behind an admin-confirmed secure view.
    sensitive_column_patterns: str = (
        r"email|phone|ssn|social_security|password|passwd|token|secret|"
        r"credit_card|card_number|iban|passport|national_id|full_name|"
        r"first_name|last_name|birth|dob"
    )

    # --- datasource network policy (spec section 22) ---
    source_host_allowlist: str = "localhost,127.0.0.1"
    connect_timeout_seconds: int = 5

    # --- tracing ---
    trace_id_header: str = "X-Trace-Id"

    @field_validator(
        "secrets_dir",
        "results_dir",
        "metadata_dir",
        "ingestion_recipe_dir",
        "llm_api_key_file",
        mode="before",
    )
    @classmethod
    def _expand(cls, value: object) -> object:
        if isinstance(value, str):
            if not value.strip():
                return None
            return Path(value)
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def source_host_allowlist_list(self) -> list[str]:
        return [item.strip() for item in self.source_host_allowlist.split(",") if item.strip()]

    def resolved_cursor_secret(self) -> str:
        """Cursor signing key: env > secrets dir file > ephemeral (dev only)."""
        if self.cursor_secret:
            return self.cursor_secret
        secret_file = self.secrets_dir / "platform.json"
        if secret_file.exists():
            try:
                payload = json.loads(secret_file.read_text(encoding="utf-8"))
                value = payload.get("cursor_secret")
                if isinstance(value, str) and value:
                    return value
            except (OSError, json.JSONDecodeError):
                pass
        # Ephemeral fallback keeps dev usable but cursors stop validating across
        # restarts. setup-secrets writes platform.json for real runs.
        return secrets.token_urlsafe(32)


@lru_cache
def get_settings() -> Settings:
    return Settings()
