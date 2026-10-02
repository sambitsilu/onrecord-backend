"""Settings, read from environment variables (see .env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://onrecord:onrecord@localhost:5432/onrecord"

    # Auth: "firebase" verifies Google Identity Platform / Firebase ID tokens.
    # "dev" accepts "Bearer dev:<email>" and is refused when env=production.
    auth_mode: str = "firebase"
    firebase_project_id: str = ""

    # AI
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"  # pin a model; change only after running the eval
    llm_timeout_seconds: int = 180
    llm_max_retries: int = 3
    default_monthly_token_budget: int = 5_000_000  # per organization

    # Limits
    max_transcript_bytes: int = 1_000_000
    chunk_chars: int = 60_000
    ask_full_corpus_chars: int = 120_000
    stale_run_minutes: int = 20

    allowed_origins: str = ""  # comma-separated, e.g. https://app.example.com

    @field_validator("auth_mode")
    @classmethod
    def _auth_mode(cls, v: str) -> str:
        if v not in ("firebase", "dev"):
            raise ValueError("AUTH_MODE must be 'firebase' or 'dev'")
        return v

    def check_production_safety(self) -> None:
        if self.env == "production":
            problems = []
            if self.auth_mode != "firebase":
                problems.append("AUTH_MODE must be 'firebase' in production")
            if not self.firebase_project_id:
                problems.append("FIREBASE_PROJECT_ID is required in production")
            if not self.anthropic_api_key:
                problems.append("ANTHROPIC_API_KEY is required in production")
            if not self.allowed_origins:
                problems.append("ALLOWED_ORIGINS is required in production")
            if problems:
                raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
