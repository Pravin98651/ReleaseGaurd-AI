"""Pydantic v2 schemas for the Intelligent Release Risk Engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    DataSufficiency,
    DimensionStatus,
    FlagSuitability,
    MitigationStatus,
    RiskAssessmentStatus,
    RiskClassification,
)


class RiskDimensionResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    dimension_key: str
    dimension_name: str
    score: int | None = None
    weight: float
    weighted_contribution: float | None = None
    severity: RiskClassification
    status: DimensionStatus
    data_sufficiency: DataSufficiency
    evidence_quality: str
    explanations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    details_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RiskFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    dimension_key: str
    finding_type: str
    title: str
    description: str
    severity: RiskClassification
    is_ai_inferred: bool
    affected_module: str | None = None
    source_reference: str | None = None
    evidence_summary: str | None = None
    status: str
    created_at: datetime


class RiskEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    finding_id: uuid.UUID | None = None
    evidence_type: str
    source_type: str
    source_reference: str
    title: str
    summary: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime


class RiskMitigationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    finding_id: uuid.UUID | None = None
    dimension_key: str
    title: str
    description: str
    priority: RiskClassification
    mitigation_type: str
    status: MitigationStatus
    required: bool
    suggested_role: str | None = None
    verification_criteria: str | None = None
    verification_evidence: str | None = None
    accepted_by_id: uuid.UUID | None = None
    completed_by_id: uuid.UUID | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RiskScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    title: str
    description: str
    trigger_condition: str
    affected_component: str
    potential_impact: str
    severity: RiskClassification
    likelihood: str | None = None
    is_potential: bool
    supporting_evidence: list[str] = Field(default_factory=list)
    detection_strategy: str | None = None
    mitigation_summary: str | None = None
    related_dimensions: list[str] = Field(default_factory=list)
    created_at: datetime


class ReleaseRiskAssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_key: str
    release_id: uuid.UUID | None = None
    pull_request_id: uuid.UUID | None = None
    repository_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None
    target_environment: str
    commit_sha: str | None = None
    release_version: int | None = None
    assessment_status: RiskAssessmentStatus
    is_current: bool
    risk_score: int | None = None
    risk_level: RiskClassification
    assessment_confidence: float | None = None
    data_completeness: float
    flag_suitability: FlagSuitability
    scoring_version: int
    analyzer_version: str
    evaluated_dimensions: list[str] = Field(default_factory=list)
    unavailable_dimensions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    critical_overrides: list[str] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    dependency_graph: dict[str, Any] = Field(default_factory=dict)
    ai_interpretation: str | None = None
    fallback_mode: bool
    created_by_id: uuid.UUID | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RiskAssessmentDetailOut(BaseModel):
    assessment: ReleaseRiskAssessmentOut
    dimensions: list[RiskDimensionResultOut] = Field(default_factory=list)
    findings: list[RiskFindingOut] = Field(default_factory=list)
    evidence: list[RiskEvidenceOut] = Field(default_factory=list)
    mitigations: list[RiskMitigationOut] = Field(default_factory=list)
    scenarios: list[RiskScenarioOut] = Field(default_factory=list)


class RiskAssessmentCreateRequest(BaseModel):
    release_id: uuid.UUID | None = None
    pull_request_id: uuid.UUID | None = None
    repository_id: uuid.UUID | None = None
    target_environment: str | None = Field(default=None, max_length=64)
    files_override: list[dict[str, Any]] | None = None
    coverage_report: dict[str, Any] | None = None
    create_execution: bool = True


class RiskMitigationCreateRequest(BaseModel):
    dimension_key: str = Field(default="operational_readiness", max_length=64)
    title: str = Field(..., min_length=2, max_length=255)
    description: str = Field(..., min_length=2)
    priority: RiskClassification = RiskClassification.HIGH
    mitigation_type: str = Field(default="manual_verification", max_length=64)
    required: bool = True
    verification_criteria: str | None = None


class RiskMitigationUpdateRequest(BaseModel):
    status: MitigationStatus | None = None
    verification_evidence: str | None = None


class RiskEngineConfigurationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    config_version: int
    is_active: bool
    dimension_weights: dict[str, float]
    risk_thresholds: dict[str, int]
    critical_override_rules: list[str]
    updated_by_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class RiskEngineConfigurationUpdateRequest(BaseModel):
    dimension_weights: dict[str, float] | None = None
    risk_thresholds: dict[str, int] | None = None
    critical_override_rules: list[str] | None = None
