"""Application settings covering all 44 ReleaseGuard AI environment variables."""

from __future__ import annotations

import base64
import hashlib
import logging
import warnings
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("releaseguard.config")


def derive_dev_fernet_key(secret_key: str) -> str:
    """Deterministically derive a valid 32-byte URL-safe base64 Fernet key for dev/test."""
    digest = hashlib.sha256(f"releaseguard-dev-fernet:{secret_key}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


class Settings(BaseSettings):
    """Pydantic v2 settings matching all 44 env vars in docs/api.md Section 4.1."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Core Backend Runtime (1-7) ---
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3001"
    SECRET_KEY: str = "change-me-32-char-random-string"
    ENCRYPTION_KEY: str = ""

    # --- Database (PostgreSQL Primary + SQLite Local Fallback) (8-14) ---
    POSTGRES_USER: str = "releaseguard"
    POSTGRES_PASSWORD: str = "releaseguard"
    POSTGRES_DB: str = "releaseguard"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = (
        "postgresql+asyncpg://releaseguard:releaseguard@postgres:5432/releaseguard"
    )
    USE_SQLITE_FALLBACK: bool = False
    SQLITE_FALLBACK_URL: str = "sqlite+aiosqlite:///./releaseguard_local.db"

    # --- Gemini AI Orchestrator (15-16) ---
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # --- GitHub Integration (17-20) ---
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_OAUTH_REDIRECT_URI: str = "http://localhost:3000/settings?github_callback=1"
    GITHUB_PAT: str = ""

    # --- Flagsmith Integration (21-27) ---
    FLAGSMITH_API_URL: str = "https://api.flagsmith.com/api/v1"
    FLAGSMITH_EDGE_API_URL: str = "https://edge.api.flagsmith.com/api/v1"
    FLAGSMITH_ORG_TOKEN: str = ""
    FLAGSMITH_PROJECT_ID: str = ""
    FLAGSMITH_DEV_ENV_KEY: str = ""
    FLAGSMITH_STAGING_ENV_KEY: str = ""
    FLAGSMITH_PROD_ENV_KEY: str = ""

    # --- Policy Engine, Bayesian Analytics & Patch Validation (28-34) ---
    APPROVAL_TTL_SECONDS: int = Field(default=900, ge=60, le=86400)
    CATEGORY_B_REQUIRES_CONFIRMATION: bool = True
    BAYESIAN_MC_SAMPLES: int = Field(default=50000, ge=100)
    BAYESIAN_MC_SEED: int = 42
    BAYESIAN_MIN_EXPOSURES: int = Field(default=100, ge=1)
    EVENT_INGEST_KEY: str = "dev-ingest-key-change-me"
    PATCH_VALIDATION_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=300)

    # --- ReleaseGuard Frontend Control Plane (35-37) ---
    FRONTEND_PORT: int = 3000
    NEXT_PUBLIC_API_BASE_URL: str = "http://localhost:8000"
    NEXT_PUBLIC_DEMO_APP_URL: str = "http://localhost:3001"

    # --- Demo Application (38-44) ---
    DEMO_BACKEND_PORT: int = 8001
    DEMO_FRONTEND_PORT: int = 3001
    NEXT_PUBLIC_DEMO_API_URL: str = "http://localhost:8001"
    NEXT_PUBLIC_FLAGSMITH_ENV_KEY: str = ""
    FLAGSMITH_SERVER_ENV_KEY: str = ""
    RELEASEGUARD_API_URL: str = "http://localhost:8000"
    RELEASEGUARD_INGEST_KEY: str = "dev-ingest-key-change-me"

    # Internal metadata (not read as an env var)
    encryption_key_derived: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _validate_security_and_urls(self) -> Settings:
        env = self.APP_ENV.strip().lower()
        self.APP_ENV = env or "development"
        self.FLAGSMITH_API_URL = self.FLAGSMITH_API_URL.rstrip("/")
        self.FLAGSMITH_EDGE_API_URL = self.FLAGSMITH_EDGE_API_URL.rstrip("/")

        raw_key = self.ENCRYPTION_KEY.strip()
        if not raw_key:
            if self.APP_ENV == "production":
                raise ValueError(
                    "ENCRYPTION_KEY is required when APP_ENV=production. "
                    'Generate one with: python -c "from cryptography.fernet import Fernet; '
                    'print(Fernet.generate_key().decode())"'
                )
            derived = derive_dev_fernet_key(self.SECRET_KEY)
            self.ENCRYPTION_KEY = derived
            self.encryption_key_derived = True
            msg = (
                "ENCRYPTION_KEY is not set; deterministically derived a development Fernet key "
                "from SECRET_KEY. Set ENCRYPTION_KEY explicitly in production."
            )
            logger.warning(msg)
            warnings.warn(msg, UserWarning, stacklevel=2)
        else:
            try:
                Fernet(raw_key.encode("ascii"))
            except Exception as exc:
                raise ValueError(
                    "ENCRYPTION_KEY must be a valid 32-byte URL-safe base64 Fernet key."
                ) from exc
            self.ENCRYPTION_KEY = raw_key
            self.encryption_key_derived = False

        if self.APP_ENV == "production" and (
            not self.SECRET_KEY.strip()
            or self.SECRET_KEY.strip() == "change-me-32-char-random-string"
        ):
            raise ValueError("SECRET_KEY must be set to a strong secret when APP_ENV=production.")

        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """Return parsed list of allowed CORS origins."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def effective_database_url(self) -> str:
        """Resolve PostgreSQL vs SQLite fallback async connection URL."""
        if self.USE_SQLITE_FALLBACK or not self.DATABASE_URL.strip():
            return self.SQLITE_FALLBACK_URL.strip() or "sqlite+aiosqlite:///./releaseguard_local.db"
        return self.DATABASE_URL.strip()

    @property
    def is_sqlite(self) -> bool:
        """True when active database URL uses SQLite."""
        return self.effective_database_url.lower().startswith("sqlite")

    @property
    def database_backend(self) -> str:
        """Return 'sqlite' or 'postgresql' for health reporting."""
        return "sqlite" if self.is_sqlite else "postgresql"

    @property
    def flagsmith_project_id_int(self) -> int | None:
        """Parse FLAGSMITH_PROJECT_ID into an int if configured."""
        raw = str(self.FLAGSMITH_PROJECT_ID).strip()
        return int(raw) if raw else None

    @property
    def flagsmith_env_keys(self) -> dict[str, str]:
        """Map non-empty configured Flagsmith environment keys by environment name."""
        mapping: dict[str, str] = {}
        if self.FLAGSMITH_DEV_ENV_KEY.strip():
            mapping["development"] = self.FLAGSMITH_DEV_ENV_KEY.strip()
        if self.FLAGSMITH_STAGING_ENV_KEY.strip():
            mapping["staging"] = self.FLAGSMITH_STAGING_ENV_KEY.strip()
        if self.FLAGSMITH_PROD_ENV_KEY.strip():
            mapping["production"] = self.FLAGSMITH_PROD_ENV_KEY.strip()
        return mapping

    def secret_values_for_scrubbing(self) -> list[str]:
        """Return non-empty secret strings that must never appear in logs."""
        candidates = [
            self.SECRET_KEY,
            self.ENCRYPTION_KEY,
            self.GEMINI_API_KEY,
            self.GITHUB_CLIENT_SECRET,
            self.GITHUB_PAT,
            self.FLAGSMITH_ORG_TOKEN,
            self.FLAGSMITH_DEV_ENV_KEY,
            self.FLAGSMITH_STAGING_ENV_KEY,
            self.FLAGSMITH_PROD_ENV_KEY,
            self.EVENT_INGEST_KEY,
            self.RELEASEGUARD_INGEST_KEY,
            self.POSTGRES_PASSWORD,
        ]
        # Ignore placeholder defaults that are short or non-secret tokens if unset
        return [
            val
            for val in candidates
            if isinstance(val, str)
            and len(val.strip()) >= 8
            and val.strip() != "your-gemini-api-key"
        ]

    def safe_summary(self) -> dict[str, Any]:
        """Return non-secret configuration diagnostics."""
        return {
            "app_env": self.APP_ENV,
            "database_backend": self.database_backend,
            "gemini_configured": bool(self.GEMINI_API_KEY.strip()),
            "gemini_model": self.GEMINI_MODEL,
            "encryption_key_derived": self.encryption_key_derived,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings singleton."""
    return Settings()


settings = get_settings()
