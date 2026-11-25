"""Configuration settings for the NovaShop demo backend."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DemoSettings(BaseSettings):
    """Environment-backed configuration for the demo checkout service."""

    DEMO_BACKEND_PORT: int = 8001
    FLAGSMITH_EDGE_API_URL: str = "https://edge.api.flagsmith.com/api/v1"
    FLAGSMITH_SERVER_ENV_KEY: str = ""
    FLAGSMITH_ENVIRONMENT_NAME: str = "development"
    CHECKOUT_FLAG_KEY: str = "checkout_v2"
    RELEASEGUARD_API_URL: str = "http://localhost:8000"
    RELEASEGUARD_INGEST_KEY: str = "dev-ingest-key-change-me"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3001"
    PSEUDONYM_SALT: str = "novashop-releaseguard-demo"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins cleanly."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> DemoSettings:
    """Return cached demo backend settings."""
    return DemoSettings()
