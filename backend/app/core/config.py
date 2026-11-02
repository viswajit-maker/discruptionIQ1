"""Environment-backed application configuration."""
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILES = (
    str(_BACKEND_DIR / ".env"),
    "backend/.env",
    ".env",
)


def _parse_origins(value: object) -> list[str]:
    """Accept a comma-delimited CORS_ORIGINS value or a list."""
    if isinstance(value, str):
        return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
    if isinstance(value, list):
        return [str(origin).strip().rstrip("/") for origin in value if str(origin).strip()]
    raise ValueError("CORS_ORIGINS must be a comma-delimited string or a list")


CorsOrigins = Annotated[list[str], NoDecode, BeforeValidator(_parse_origins)]


class Settings(BaseSettings):
    """Settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(env_file=_ENV_FILES, env_file_encoding="utf-8", extra="ignore")

    project_name: str = "DisruptionIQ API"
    version: str = "0.1.0"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./data/disruptioniq.db"
    cors_origins: CorsOrigins = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    # This fixed clock keeps simulations and their tests reproducible. Set
    # SIMULATION_CLOCK to an ISO-8601 timestamp to run another scenario.
    simulation_clock: datetime = Field(default_factory=lambda: datetime(2026, 10, 26, 18, tzinfo=timezone.utc))
    risk_weight_delay: int = 45
    risk_weight_shortage: int = 35
    risk_weight_deadline: int = 20

    # Modular AI assistance layer configuration
    ai_enabled: bool = False
    llm_provider: str = "mock"  # "mock", "openai" (OpenAI-compatible)
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 8.0
    llm_max_retries: int = 1

    # Authentication & Authorization configuration
    auth_required: bool = False  # Enforce strict Authorization header on all endpoints; defaults to False in dev for seamless backward compatibility, set True in production
    jwt_secret_key: str = "disruptioniq-prod-secret-change-in-env-9f3a8b2c"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    procurement_approval_threshold_usd: float = 50000.0
    rate_limit_enabled: bool = True
    rate_limit_login_max_attempts: int = 5
    rate_limit_window_seconds: int = 60

    # Email notifications via Resend
    resend_api_key: str | None = None
    approver_email: str | None = None
    email_from: str = "onboarding@resend.dev"
    app_base_url: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
