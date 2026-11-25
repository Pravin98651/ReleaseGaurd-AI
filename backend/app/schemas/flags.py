"""Pydantic v2 schemas for feature flags, gradual rollouts, and flag health."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import FlagType
from app.schemas.common import ORMBaseModel


class MultivariateOptionSchema(BaseModel):
    """Flagsmith multivariate option definition."""

    value: str = Field(min_length=1, max_length=255)
    default_percentage_allocation: int = Field(ge=0, le=100)


class EnvStateSchema(BaseModel):
    """Per-environment Flagsmith state cached in feature_flags.env_states."""

    env_key: str = ""
    is_production: bool = True
    use_v2_versioning: bool = False
    enabled: bool = False
    value: Any = None
    rollout_percentage: int = Field(default=0, ge=0, le=100)
    mv_options: list[dict[str, Any]] = Field(default_factory=list)
    segment_overrides: list[dict[str, Any]] = Field(default_factory=list)
    version_uuid: str | None = None


class FeatureFlagCreate(BaseModel):
    """Create a Flagsmith feature flag (Category B)."""

    project_id: int | None = Field(default=None, ge=1)
    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    description: str = ""
    flag_type: FlagType = FlagType.STANDARD
    initial_value: str | None = None
    mv_options: list[MultivariateOptionSchema] = Field(default_factory=list)


class FeatureFlagOut(ORMBaseModel):
    """Synchronized feature flag representation."""

    id: UUID
    flagsmith_feature_id: int | None = None
    flagsmith_project_id: int
    repository_id: UUID | None = None
    key: str
    description: str | None = None
    flag_type: FlagType
    is_archived: bool
    env_states: dict[str, Any] = Field(default_factory=dict)
    mv_options: list[dict[str, Any]] = Field(default_factory=list)
    code_references: list[dict[str, Any]] = Field(default_factory=list)
    health_score: int
    health_issues: list[dict[str, Any]] = Field(default_factory=list)
    last_synced_at: datetime | None = None
    last_health_scan_at: datetime | None = None
    flagsmith_created_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FlagToggleRequest(BaseModel):
    """Enable/disable toggle request in an environment."""

    environment_name: str = Field(min_length=1, max_length=64)
    enabled: bool
    reason: str = Field(default="Operator toggle request", min_length=1)


class FlagRolloutRequest(BaseModel):
    """Gradual rollout stage request (10->25->50->75->100%)."""

    environment_name: str = Field(min_length=1, max_length=64)
    target_percentage: Literal[0, 10, 25, 50, 75, 100]
    strategy: Literal["percentage_split_segment", "multivariate"] = "percentage_split_segment"
    variation_key: str | None = None
    reason: str = Field(default="Gradual rollout progression", min_length=1)


class FlagRollbackRequest(BaseModel):
    """Rollback feature flag to 0% / disabled."""

    environment_name: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1)


class FlagNLCommandRequest(BaseModel):
    """Natural-language flag management command routed to flag_agent."""

    command: str = Field(min_length=1, max_length=2000)
    project_id: int | None = None
    environment_name: str | None = None
    session_id: UUID | None = None


class HealthIssueSchema(BaseModel):
    """Unified explainable Flag Health issue matching docs/database-and-policies.md & #9."""

    issue_type: Literal[
        "stale",
        "unused",
        "always_on",
        "always_off",
        "orphaned",
        "duplicate",
        "env_drift",
    ]
    penalty: int = Field(le=0)
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class FlagHealthItem(BaseModel):
    """Per-flag health summary sorted by health_score."""

    flag_id: UUID
    key: str
    flag_type: FlagType
    health_score: int = Field(ge=0, le=100)
    health_issues: list[HealthIssueSchema] = Field(default_factory=list)
    code_references_count: int = Field(ge=0)
    last_health_scan_at: datetime | None = None


class FlagHealthScanResponse(BaseModel):
    """Aggregate summary from POST /api/v1/flag-health/scan."""

    flags_scanned: int = Field(ge=0)
    unhealthy_flags: int = Field(ge=0)
    average_health_score: float = Field(ge=0.0, le=100.0)
    issues_by_type: dict[str, int] = Field(default_factory=dict)
    flags: list[FlagHealthItem] = Field(default_factory=list)


class CleanupPRRequest(BaseModel):
    """Request to generate a flag cleanup patch and optionally open a Draft PR."""

    repository_id: UUID
    keep_branch: Literal["enabled", "disabled"] = "enabled"
    open_draft_pr: bool = False


class FlagDeleteRequest(BaseModel):
    """Category C request to archive or delete a Flagsmith flag (never auto-executed)."""

    action: Literal["archive", "delete"] = "archive"
    reason: str = Field(min_length=1)
