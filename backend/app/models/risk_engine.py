"""Intelligent Release Risk Engine ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    DataSufficiency,
    DimensionStatus,
    FlagSuitability,
    MitigationStatus,
    RiskAssessmentStatus,
    RiskClassification,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.command_center import AgentExecution
    from app.models.github import PullRequest, Repository
    from app.models.identity import User
    from app.models.releases import Release


def default_dimension_weights() -> dict[str, float]:
    return {
        "code_impact": 0.20,
        "dependency_impact": 0.15,
        "historical_failures": 0.15,
        "test_coverage": 0.15,
        "production_environment": 0.15,
        "feature_flag_suitability": 0.10,
        "operational_readiness": 0.10,
    }


def default_risk_thresholds() -> dict[str, int]:
    return {
        "low_max": 19,
        "moderate_max": 39,
        "elevated_max": 59,
        "high_max": 79,
    }


class RiskEngineConfiguration(Base):
    """Versioned, audited scoring weights and threshold rules."""

    __tablename__ = "risk_engine_configurations"
    __table_args__ = (
        UniqueConstraint("config_version", name="uq_risk_engine_config_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dimension_weights: Mapped[dict[str, float]] = mapped_column(
        JSONBCompat, nullable=False, default=default_dimension_weights
    )
    risk_thresholds: Mapped[dict[str, int]] = mapped_column(
        JSONBCompat, nullable=False, default=default_risk_thresholds
    )
    critical_override_rules: Mapped[list[str]] = mapped_column(
        JSONBCompat,
        nullable=False,
        default=lambda: [
            "destructive_migration_without_strategy",
            "failed_mandatory_validation",
            "unresolved_critical_incident",
            "missing_prod_rollback_path",
        ],
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class ReleaseRiskAssessment(Base):
    """Persistent, versioned multi-dimensional release risk assessment."""

    __tablename__ = "release_risk_assessments"
    __table_args__ = (
        UniqueConstraint("assessment_key", name="uq_risk_assessments_key"),
        Index("ix_risk_assessments_release_created", "release_id", "created_at"),
        Index("ix_risk_assessments_pr_created", "pull_request_id", "created_at"),
        Index("ix_risk_assessments_level", "risk_level", "assessment_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_key: Mapped[str] = mapped_column(String(48), nullable=False)
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    pull_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pull_requests.id", ondelete="SET NULL"), nullable=True
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL"), nullable=True
    )

    target_environment: Mapped[str] = mapped_column(String(64), nullable=False, default="development")
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assessment_status: Mapped[RiskAssessmentStatus] = mapped_column(
        str_enum(RiskAssessmentStatus, length=32),
        nullable=False,
        default=RiskAssessmentStatus.PENDING,
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=32),
        nullable=False,
        default=RiskClassification.MODERATE,
    )
    assessment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    flag_suitability: Mapped[FlagSuitability] = mapped_column(
        str_enum(FlagSuitability, length=40),
        nullable=False,
        default=FlagSuitability.INSUFFICIENT_INFORMATION,
    )

    scoring_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    analyzer_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    evaluated_dimensions: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    unavailable_dimensions: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    limitations: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    critical_overrides: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    dependency_graph: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    ai_interpretation: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    release: Mapped[Release | None] = relationship()
    pull_request: Mapped[PullRequest | None] = relationship()
    repository: Mapped[Repository | None] = relationship()
    execution: Mapped[AgentExecution | None] = relationship()
    created_by: Mapped[User | None] = relationship()

    dimensions: Mapped[list[RiskDimensionResult]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )
    findings: Mapped[list[RiskFinding]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )
    evidence_items: Mapped[list[RiskEvidence]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )
    mitigations: Mapped[list[RiskMitigation]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )
    scenarios: Mapped[list[RiskScenario]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class RiskDimensionResult(Base):
    """Per-dimension score, weight, data sufficiency, and explanation."""

    __tablename__ = "risk_dimension_results"
    __table_args__ = (
        UniqueConstraint("assessment_id", "dimension_key", name="uq_risk_dim_assess_key"),
        Index("ix_risk_dim_assess", "assessment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("release_risk_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension_name: Mapped[str] = mapped_column(String(128), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    weighted_contribution: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=32),
        nullable=False,
        default=RiskClassification.MODERATE,
    )
    status: Mapped[DimensionStatus] = mapped_column(
        str_enum(DimensionStatus, length=32),
        nullable=False,
        default=DimensionStatus.EVALUATED,
    )
    data_sufficiency: Mapped[DataSufficiency] = mapped_column(
        str_enum(DataSufficiency, length=32),
        nullable=False,
        default=DataSufficiency.SUFFICIENT,
    )
    evidence_quality: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    explanations: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    assessment: Mapped[ReleaseRiskAssessment] = relationship(back_populates="dimensions")


class RiskFinding(Base):
    """Specific technical or operational finding produced by an analyzer."""

    __tablename__ = "risk_findings"
    __table_args__ = (
        Index("ix_risk_findings_assess_dim", "assessment_id", "dimension_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("release_risk_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension_key: Mapped[str] = mapped_column(String(64), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=32),
        nullable=False,
        default=RiskClassification.MODERATE,
    )
    is_ai_inferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    affected_module: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    assessment: Mapped[ReleaseRiskAssessment] = relationship(back_populates="findings")


class RiskEvidence(Base):
    """Traceable source evidence backing findings and dimensions."""

    __tablename__ = "risk_evidence"
    __table_args__ = (
        Index("ix_risk_evidence_assess", "assessment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("release_risk_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_findings.id", ondelete="SET NULL"), nullable=True
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    collected_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    assessment: Mapped[ReleaseRiskAssessment] = relationship(back_populates="evidence_items")


class RiskMitigation(Base):
    """Actionable mitigation recommendation with verification tracking."""

    __tablename__ = "risk_mitigations"
    __table_args__ = (
        Index("ix_risk_mitigations_assess_status", "assessment_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("release_risk_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_findings.id", ondelete="SET NULL"), nullable=True
    )
    dimension_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=32),
        nullable=False,
        default=RiskClassification.HIGH,
    )
    mitigation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[MitigationStatus] = mapped_column(
        str_enum(MitigationStatus, length=32),
        nullable=False,
        default=MitigationStatus.PENDING,
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    suggested_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    assessment: Mapped[ReleaseRiskAssessment] = relationship(back_populates="mitigations")


class RiskScenario(Base):
    """Potential failure scenario grounded in risk findings."""

    __tablename__ = "risk_scenarios"
    __table_args__ = (
        Index("ix_risk_scenarios_assess", "assessment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("release_risk_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_condition: Mapped[str] = mapped_column(Text, nullable=False)
    affected_component: Mapped[str] = mapped_column(String(255), nullable=False)
    potential_impact: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=32),
        nullable=False,
        default=RiskClassification.HIGH,
    )
    likelihood: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_potential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supporting_evidence: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    detection_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    mitigation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_dimensions: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    assessment: Mapped[ReleaseRiskAssessment] = relationship(back_populates="scenarios")
