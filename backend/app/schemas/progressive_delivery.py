"""Pydantic v2 schemas for the Progressive Delivery Engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActorType,
    GuardrailEvalStatus,
    GuardrailOperator,
    ProgressiveRolloutStatus,
    ProgressiveStageStatus,
    RiskClassification,
    RollbackOperationStatus,
    RolloutDecisionType,
)


class ProgressiveRolloutStageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_id: uuid.UUID
    stage_number: int
    target_percentage: int
    status: ProgressiveStageStatus
    observation_started_at: datetime | None = None
    observation_ends_at: datetime | None = None
    minimum_observation_seconds: int
    maximum_observation_seconds: int
    minimum_sample_size: int
    actual_sample_size: int
    healthy_evaluations_count: int
    activated_at: datetime | None = None
    completed_at: datetime | None = None
    evaluation_status: str | None = None
    decision: RolloutDecisionType | None = None
    approval_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class RolloutMetricSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_id: uuid.UUID
    stage_id: uuid.UUID | None = None
    metric_key: str
    metric_value: float
    sample_size: int
    numerator: float | None = None
    denominator: float | None = None
    unit: str
    window_start: datetime
    window_end: datetime
    data_source: str
    freshness_status: str
    created_at: datetime


class RolloutGuardrailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_id: uuid.UUID
    metric_key: str
    operator: GuardrailOperator
    threshold: float
    severity: RiskClassification
    evaluation_window_seconds: int
    minimum_sample_size: int
    consecutive_breach_count: int
    current_breach_streak: int
    action: str
    enabled: bool
    created_at: datetime


class GuardrailEvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_id: uuid.UUID
    stage_id: uuid.UUID | None = None
    guardrail_id: uuid.UUID
    observed_value: float | None = None
    threshold: float
    sample_size: int
    status: GuardrailEvalStatus
    severity: RiskClassification
    evidence: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime


class RolloutDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_id: uuid.UUID
    stage_id: uuid.UUID | None = None
    decision_type: RolloutDecisionType
    decision_summary: str
    supporting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    decision_source: str
    created_at: datetime


class ProgressiveRolloutEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_id: uuid.UUID
    stage_id: uuid.UUID | None = None
    event_type: str
    actor_type: ActorType
    actor_id: uuid.UUID | None = None
    message: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RollbackOperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_id: uuid.UUID
    stage_id: uuid.UUID | None = None
    feature_flag_id: uuid.UUID
    environment: str
    previous_configuration: dict[str, Any] = Field(default_factory=dict)
    requested_configuration: dict[str, Any] = Field(default_factory=dict)
    status: RollbackOperationStatus
    reason: str
    approval_id: uuid.UUID | None = None
    verification_result: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class ProgressiveRolloutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rollout_key: str
    name: str
    description: str | None = None
    release_id: uuid.UUID | None = None
    repository_id: uuid.UUID | None = None
    pull_request_id: uuid.UUID | None = None
    commit_sha: str | None = None
    feature_flag_id: uuid.UUID
    feature_flag_key: str
    experiment_id: uuid.UUID | None = None
    environment: str
    status: ProgressiveRolloutStatus
    current_stage_id: uuid.UUID | None = None
    current_percentage: int
    target_percentage: int
    primary_metric_key: str
    rollout_configuration: dict[str, Any] = Field(default_factory=dict)
    observation_configuration: dict[str, Any] = Field(default_factory=dict)
    rollback_configuration: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class ProgressiveRolloutDetailOut(BaseModel):
    rollout: ProgressiveRolloutOut
    stages: list[ProgressiveRolloutStageOut] = Field(default_factory=list)
    guardrails: list[RolloutGuardrailOut] = Field(default_factory=list)
    metrics: list[RolloutMetricSnapshotOut] = Field(default_factory=list)
    evaluations: list[GuardrailEvaluationOut] = Field(default_factory=list)
    decisions: list[RolloutDecisionOut] = Field(default_factory=list)
    events: list[ProgressiveRolloutEventOut] = Field(default_factory=list)
    rollbacks: list[RollbackOperationOut] = Field(default_factory=list)


class ProgressiveRolloutCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: str | None = None
    feature_flag_id: uuid.UUID
    environment: str = Field(default="development", max_length=64)
    release_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    stage_percentages: list[int] | None = None
    primary_metric_key: str = Field(default="checkout_completed", max_length=64)
    observation_config: dict[str, Any] | None = None
    guardrails: list[dict[str, Any]] | None = None


class RolloutControlRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=1000)
