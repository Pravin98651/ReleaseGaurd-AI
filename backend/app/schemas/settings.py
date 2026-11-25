"""Pydantic v2 schemas for /settings and /integrations endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    AnalyticsMode,
    IntegrationAuthMode,
    IntegrationProvider,
    IntegrationStatus,
)
from app.schemas.common import ORMBaseModel


class GuardrailThresholdsSchema(BaseModel):
    """Unified guardrail thresholds matching docs/database-and-policies.md & #9."""

    min_exposures: int = Field(default=100, ge=1)
    max_expected_loss: float = Field(default=0.005, ge=0.0, le=1.0)
    guardrail_max_error_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    soft_error_delta: float = Field(default=0.015, ge=0.0, le=1.0)
    hard_error_delta: float = Field(default=0.030, ge=0.0, le=1.0)


class HealthThresholdsSchema(BaseModel):
    """Flag Health Monitor lookback day thresholds."""

    stale_days: int = Field(default=30, ge=1, le=3650)
    unused_days: int = Field(default=14, ge=1, le=3650)
    always_on_days: int = Field(default=30, ge=1, le=3650)
    always_off_days: int = Field(default=30, ge=1, le=3650)


class IntegrationConnectionOut(ORMBaseModel):
    """Safe masked integration summary (never exposes raw or encrypted tokens)."""

    id: UUID
    provider: IntegrationProvider
    auth_mode: IntegrationAuthMode
    status: IntegrationStatus
    external_account_id: str | None = None
    external_account_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    token_hint: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    last_verified_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceSettingsOut(ORMBaseModel):
    """Workspace policy, model, and analytics configuration plus masked integrations."""

    id: UUID
    singleton_key: str = "default"
    category_b_requires_confirmation: bool
    approval_ttl_seconds: int
    gemini_model: str
    flagsmith_analytics_mode: AnalyticsMode
    flagsmith_capability_probed_at: datetime | None = None
    default_guardrails: GuardrailThresholdsSchema
    health_thresholds: HealthThresholdsSchema
    updated_at: datetime
    integrations: list[IntegrationConnectionOut] = Field(default_factory=list)


class WorkspaceSettingsUpdate(BaseModel):
    """Partial update payload for PATCH /api/v1/settings."""

    category_b_requires_confirmation: bool | None = None
    approval_ttl_seconds: int | None = Field(default=None, ge=60, le=86400)
    gemini_model: str | None = Field(default=None, min_length=1, max_length=100)
    default_guardrails: GuardrailThresholdsSchema | None = None
    health_thresholds: HealthThresholdsSchema | None = None


class GitHubOAuthStartResponse(BaseModel):
    """OAuth authorize URL and signed CSRF state."""

    authorize_url: str
    state: str


class GitHubOAuthCallbackRequest(BaseModel):
    """OAuth callback exchange request."""

    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


class GitHubPatConnectRequest(BaseModel):
    """Fine-grained PAT connection payload."""

    token: str = Field(min_length=4)
    account_label: str | None = None


class FlagsmithConnectRequest(BaseModel):
    """Flagsmith org token, project ID, and environment keys connection payload."""

    org_token: str = Field(min_length=4)
    project_id: int = Field(ge=1)
    environment_keys: dict[str, str] = Field(default_factory=dict)


class FlagsmithProbeRequest(BaseModel):
    """Optional target environment key for capability probe."""

    environment_key: str | None = None


class FlagsmithProbeResponse(BaseModel):
    """Capability probe result indicating native vs tracked_event_fallback mode."""

    analytics_mode: AnalyticsMode
    native_supported: bool
    probed_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
