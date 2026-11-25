"""Release Intelligence & Learning Engine ORM models (Phase 5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    AnalysisRunStatus,
    EvidenceClassification,
    FailureCategory,
    PolicyArea,
    PolicyReviewStatus,
    RecommendationOutcomeClass,
    ReleaseOutcomeType,
    RiskClassification,
    str_enum,
)


class ReleaseOutcome(Base):
    """Normalized historical outcome record for a release."""

    __tablename__ = "release_outcomes"
    __table_args__ = (
        Index("ix_release_outcomes_release_unique", "release_id", unique=True),
        Index("ix_release_outcomes_env_type", "environment", "outcome_type"),
        Index("ix_release_outcomes_recorded", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    outcome_type: Mapped[ReleaseOutcomeType] = mapped_column(
        str_enum(ReleaseOutcomeType, length=40), nullable=False, default=ReleaseOutcomeType.UNKNOWN
    )
    failure_category: Mapped[FailureCategory | None] = mapped_column(
        str_enum(FailureCategory, length=50), nullable=True
    )
    contributing_categories: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    final_status: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False, default="development")
    release_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovery_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_latency_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rollback_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_classification: Mapped[EvidenceClassification] = mapped_column(
        str_enum(EvidenceClassification, length=32),
        nullable=False,
        default=EvidenceClassification.OBSERVED,
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    data_quality_flags: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recorded_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class ReleaseFailurePattern(Base):
    """Recurring failure pattern detected across historical releases."""

    __tablename__ = "release_failure_patterns"
    __table_args__ = (
        Index("ix_failure_patterns_key_unique", "pattern_key", unique=True),
        Index("ix_failure_patterns_category", "category"),
        Index("ix_failure_patterns_repo", "repository_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pattern_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[FailureCategory] = mapped_column(
        str_enum(FailureCategory, length=50), nullable=False, default=FailureCategory.UNKNOWN
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    affected_module: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dependency_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_quality: Mapped[EvidenceClassification] = mapped_column(
        str_enum(EvidenceClassification, length=32),
        nullable=False,
        default=EvidenceClassification.OBSERVED,
    )
    is_exploratory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confidence_data: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    supporting_release_ids: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    recommended_mitigation: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_observed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_analyzed_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class RepositoryIntelligenceProfile(Base):
    """Per-repository longitudinal reliability & risk profile."""

    __tablename__ = "repository_intelligence_profiles"
    __table_args__ = (
        Index("ix_repo_intel_repo_unique", "repository_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    release_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rollback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    historical_failure_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_recovery_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    common_failure_categories: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    high_risk_modules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    dependency_patterns: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    risk_indicators: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    last_analyzed_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RecommendationOutcome(Base):
    """Evaluates whether an AI AgentDecision / recommendation achieved its expected outcome."""

    __tablename__ = "recommendation_outcomes"
    __table_args__ = (
        Index("ix_rec_outcomes_decision_unique", "recommendation_id", unique=True),
        Index("ix_rec_outcomes_class", "outcome_classification"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_decisions.id", ondelete="CASCADE"), nullable=False
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    recommendation_category: Mapped[str] = mapped_column(String(80), nullable=False, default="general")
    outcome_classification: Mapped[RecommendationOutcomeClass] = mapped_column(
        str_enum(RecommendationOutcomeClass, length=40),
        nullable=False,
        default=RecommendationOutcomeClass.NOT_EVALUABLE,
    )
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    implemented: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    actual_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    evaluation_method: Mapped[str] = mapped_column(
        String(64), nullable=False, default="deterministic_outcome_match"
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class ReleaseInsight(Base):
    """Evidence-linked organizational insight generated by ReleaseLearningEngine."""

    __tablename__ = "release_insights"
    __table_args__ = (
        Index("ix_release_insights_key_unique", "insight_key", unique=True),
        Index("ix_release_insights_category", "insight_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    insight_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    insight_category: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    evidence_classification: Mapped[EvidenceClassification] = mapped_column(
        str_enum(EvidenceClassification, length=32),
        nullable=False,
        default=EvidenceClassification.OBSERVED,
    )
    affected_repositories: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    affected_releases: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_data: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    severity: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=24), nullable=False, default=RiskClassification.MODERATE
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    generated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="learning_engine")
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class PolicyRecommendation(Base):
    """Human-governed future release policy improvement recommendation."""

    __tablename__ = "policy_recommendations"
    __table_args__ = (
        Index("ix_policy_recs_status", "review_status"),
        Index("ix_policy_recs_area", "policy_area"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    policy_area: Mapped[PolicyArea] = mapped_column(
        str_enum(PolicyArea, length=50), nullable=False, default=PolicyArea.OBSERVATION_WINDOWS
    )
    current_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    proposed_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    approved_configuration: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBCompat, nullable=True
    )
    supporting_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    affected_repositories: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    expected_benefit: Mapped[str | None] = mapped_column(Text, nullable=True)
    tradeoffs: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_data: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    review_status: Mapped[PolicyReviewStatus] = mapped_column(
        str_enum(PolicyReviewStatus, length=32),
        nullable=False,
        default=PolicyReviewStatus.PENDING_REVIEW,
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    implemented_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    verification_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class IntelligenceAnalysisRun(Base):
    """Audit and reproducibility record for Release Intelligence analysis runs."""

    __tablename__ = "intelligence_analysis_runs"
    __table_args__ = (
        Index("ix_intel_runs_type_started", "analysis_type", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_type: Mapped[str] = mapped_column(String(64), nullable=False, default="full_learning")
    scope: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    status: Mapped[AnalysisRunStatus] = mapped_column(
        str_enum(AnalysisRunStatus, length=32), nullable=False, default=AnalysisRunStatus.RUNNING
    )
    input_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    started_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
