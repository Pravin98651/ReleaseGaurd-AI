"""Pydantic v2 schemas for the Autonomous Release Lifecycle Engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActorType,
    ReleaseArtifactType,
    ReleaseCheckStatus,
    ReleaseReadinessStatus,
    ReleaseStageKey,
    ReleaseStageStatus,
    ReleaseStatus,
    ReleaseType,
    RiskLevel,
)
from app.schemas.approvals import ApprovalOut


class ReleaseStageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    stage_key: ReleaseStageKey
    stage_order: int
    title: str
    status: ReleaseStageStatus
    attempt_count: int
    max_attempts: int
    summary: str | None = None
    details_json: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ReleaseEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    event_type: str
    from_status: ReleaseStatus | None = None
    to_status: ReleaseStatus | None = None
    stage_key: ReleaseStageKey | None = None
    actor_id: uuid.UUID | None = None
    actor_type: ActorType
    title: str
    message: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ReleaseArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    artifact_type: ReleaseArtifactType
    title: str
    source_table: str | None = None
    source_id: str | None = None
    uri: str | None = None
    summary: str | None = None
    content_json: dict[str, Any] = Field(default_factory=dict)
    created_by_id: uuid.UUID | None = None
    created_at: datetime


class ReleaseCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    check_key: str
    category: str
    title: str
    status: ReleaseCheckStatus
    is_blocking: bool
    weight: int
    message: str | None = None
    details_json: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime | None = None


class ReleaseCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    release_type: ReleaseType = ReleaseType.FEATURE
    target_environment: str = Field(default="development", max_length=64)
    repository_id: uuid.UUID | None = None
    pull_request_id: uuid.UUID | None = None
    feature_flag_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    risk_level: RiskLevel | None = None
    target_rollout_percentage: int = Field(default=100, ge=0, le=100)
    scheduled_for: datetime | None = None
    ai_assist: bool = False
    allow_duplicate: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReleaseUpdateRequest(BaseModel):
    expected_version: int = Field(..., ge=1)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    target_environment: str | None = Field(default=None, max_length=64)
    feature_flag_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    risk_level: RiskLevel | None = None
    target_rollout_percentage: int | None = Field(default=None, ge=0, le=100)
    scheduled_for: datetime | None = None


class ReleaseLifecycleActionRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class ReleaseApprovalCreateRequest(BaseModel):
    rationale: str | None = Field(default=None, max_length=2000)


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_key: str
    title: str
    description: str | None = None
    release_type: ReleaseType
    target_environment: str
    status: ReleaseStatus
    previous_status: ReleaseStatus | None = None
    version: int
    repository_id: uuid.UUID | None = None
    pull_request_id: uuid.UUID | None = None
    feature_flag_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    created_by_id: uuid.UUID | None = None
    risk_level: RiskLevel
    readiness_score: int
    readiness_status: ReleaseReadinessStatus
    readiness_summary: dict[str, Any] = Field(default_factory=dict)
    target_rollout_percentage: int
    current_rollout_percentage: int
    ai_summary: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    scheduled_for: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    paused_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ReleaseDetailOut(BaseModel):
    release: ReleaseOut
    stages: list[ReleaseStageOut] = Field(default_factory=list)
    checks: list[ReleaseCheckOut] = Field(default_factory=list)
    events: list[ReleaseEventOut] = Field(default_factory=list)
    artifacts: list[ReleaseArtifactOut] = Field(default_factory=list)
    approvals: list[ApprovalOut] = Field(default_factory=list)
