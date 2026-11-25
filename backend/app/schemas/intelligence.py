"""Pydantic v2 schemas for the Release Intelligence & Learning Engine (Phase 5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AnalysisRunStatus,
    EvidenceClassification,
    FailureCategory,
    PolicyArea,
    PolicyReviewStatus,
    RecommendationOutcomeClass,
    ReleaseOutcomeType,
    RiskClassification,
)


class ReleaseOutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    repository_id: uuid.UUID | None = None
    outcome_type: ReleaseOutcomeType
    failure_category: FailureCategory | None = None
    contributing_categories: list[str] = Field(default_factory=list)
    final_status: str
    environment: str
    release_duration_seconds: int | None = None
    recovery_duration_seconds: int | None = None
    lead_time_seconds: int | None = None
    approval_latency_seconds: int | None = None
    rollback_required: bool
    evidence_classification: EvidenceClassification
    evidence: dict[str, Any] = Field(default_factory=dict)
    data_quality_flags: list[str] = Field(default_factory=list)
    verified: bool
    recorded_at: datetime
    updated_at: datetime


class ReleaseFailurePatternOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pattern_key: str
    name: str
    description: str
    category: FailureCategory
    repository_id: uuid.UUID | None = None
    affected_module: str | None = None
    dependency_reference: str | None = None
    sample_size: int
    failure_count: int
    observed_rate: float
    evidence_quality: EvidenceClassification
    is_exploratory: bool
    confidence_data: dict[str, Any] = Field(default_factory=dict)
    supporting_release_ids: list[str] = Field(default_factory=list)
    recommended_mitigation: str | None = None
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    last_analyzed_at: datetime


class RepositoryIntelligenceProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    repository_name: str | None = None
    release_count: int
    failure_count: int
    rollback_count: int
    historical_failure_rate: float
    avg_recovery_seconds: float | None = None
    common_failure_categories: list[dict[str, Any]] = Field(default_factory=list)
    high_risk_modules: list[dict[str, Any]] = Field(default_factory=list)
    dependency_patterns: list[dict[str, Any]] = Field(default_factory=list)
    risk_indicators: dict[str, Any] = Field(default_factory=dict)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    last_analyzed_at: datetime
    version: int


class RecommendationOutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recommendation_id: uuid.UUID
    release_id: uuid.UUID | None = None
    repository_id: uuid.UUID | None = None
    recommendation_category: str
    outcome_classification: RecommendationOutcomeClass
    accepted: bool | None = None
    implemented: bool
    verified: bool
    actual_outcome: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    evaluation_method: str
    evaluated_at: datetime


class ReleaseInsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    insight_key: str
    title: str
    description: str
    insight_category: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_classification: EvidenceClassification
    affected_repositories: list[str] = Field(default_factory=list)
    affected_releases: list[str] = Field(default_factory=list)
    sample_size: int
    confidence_data: dict[str, Any] = Field(default_factory=dict)
    severity: RiskClassification
    status: str
    generated_by: str
    created_at: datetime
    updated_at: datetime


class PolicyRecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    policy_area: PolicyArea
    current_configuration: dict[str, Any] = Field(default_factory=dict)
    proposed_configuration: dict[str, Any] = Field(default_factory=dict)
    approved_configuration: dict[str, Any] | None = None
    supporting_evidence: dict[str, Any] = Field(default_factory=dict)
    affected_repositories: list[str] = Field(default_factory=list)
    expected_benefit: str | None = None
    tradeoffs: str | None = None
    confidence_data: dict[str, Any] = Field(default_factory=dict)
    review_status: PolicyReviewStatus
    reviewer_id: uuid.UUID | None = None
    review_notes: str | None = None
    approval_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    implemented_at: datetime | None = None
    verified_at: datetime | None = None
    verification_evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class IntelligenceAnalysisRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    analysis_type: str
    scope: dict[str, Any] = Field(default_factory=dict)
    status: AnalysisRunStatus
    input_record_count: int
    output_record_count: int
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    model_version: str | None = None
    analysis_version: str
    started_at: datetime
    completed_at: datetime | None = None


class PolicyReviewRequest(BaseModel):
    decision: str = Field(..., description="accept | reject | modify")
    notes: str | None = None
    modified_configuration: dict[str, Any] | None = None


class PolicyActionRequest(BaseModel):
    notes: str | None = None


class IntelligenceQueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=1000)
    repository_id: uuid.UUID | None = None


class IntelligenceAnalyzeRequest(BaseModel):
    analysis_type: str = Field(default="full_learning", max_length=64)
    repository_id: uuid.UUID | None = None
