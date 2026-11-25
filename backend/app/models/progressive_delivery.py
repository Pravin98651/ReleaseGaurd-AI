"""Progressive Delivery Engine ORM models."""

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
    ActorType,
    GuardrailEvalStatus,
    GuardrailOperator,
    ProgressiveRolloutStatus,
    ProgressiveStageStatus,
    RiskClassification,
    RollbackOperationStatus,
    RolloutDecisionType,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.experiments import Experiment
    from app.models.flags import FeatureFlag
    from app.models.github import PullRequest, Repository
    from app.models.governance import Approval
    from app.models.identity import User
    from app.models.releases import Release


def default_observation_config() -> dict[str, int]:
    return {
        "minimum_observation_minutes": 15,
        "maximum_observation_minutes": 60,
        "evaluation_interval_seconds": 60,
        "minimum_sample_size": 100,
        "maximum_metric_age_seconds": 180,
        "required_healthy_evaluations": 2,
    }


class ProgressiveRollout(Base):
    """Persistent progressive delivery rollout plan (1% -> 5% -> 10% -> 25% -> 50% -> 100%)."""

    __tablename__ = "progressive_rollouts"
    __table_args__ = (
        UniqueConstraint("rollout_key", name="uq_progressive_rollouts_key"),
        Index("ix_prog_rollouts_status_env", "status", "environment"),
        Index("ix_prog_rollouts_flag_env", "feature_flag_id", "environment"),
        Index("ix_prog_rollouts_release", "release_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_key: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    pull_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pull_requests.id", ondelete="SET NULL"), nullable=True
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature_flag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False
    )
    feature_flag_key: Mapped[str] = mapped_column(String(128), nullable=False)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )

    environment: Mapped[str] = mapped_column(String(64), nullable=False, default="development")
    status: Mapped[ProgressiveRolloutStatus] = mapped_column(
        str_enum(ProgressiveRolloutStatus, length=40),
        nullable=False,
        default=ProgressiveRolloutStatus.DRAFT,
    )
    previous_status: Mapped[ProgressiveRolloutStatus | None] = mapped_column(
        str_enum(ProgressiveRolloutStatus, length=40), nullable=True
    )
    current_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    current_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    primary_metric_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="checkout_completed"
    )

    rollout_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    observation_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=default_observation_config
    )
    rollback_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    release: Mapped[Release | None] = relationship()
    repository: Mapped[Repository | None] = relationship()
    pull_request: Mapped[PullRequest | None] = relationship()
    feature_flag: Mapped[FeatureFlag] = relationship()
    experiment: Mapped[Experiment | None] = relationship()
    created_by: Mapped[User | None] = relationship()

    stages: Mapped[list[ProgressiveRolloutStage]] = relationship(
        back_populates="rollout",
        cascade="all, delete-orphan",
        order_by="ProgressiveRolloutStage.stage_number",
    )
    guardrails: Mapped[list[RolloutGuardrail]] = relationship(
        back_populates="rollout", cascade="all, delete-orphan"
    )
    metric_snapshots: Mapped[list[RolloutMetricSnapshot]] = relationship(
        back_populates="rollout", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[RolloutDecision]] = relationship(
        back_populates="rollout", cascade="all, delete-orphan"
    )
    events: Mapped[list[ProgressiveRolloutEvent]] = relationship(
        back_populates="rollout", cascade="all, delete-orphan"
    )
    rollbacks: Mapped[list[RollbackOperation]] = relationship(
        back_populates="rollout", cascade="all, delete-orphan"
    )


class ProgressiveRolloutStage(Base):
    """Ordered rollout exposure stage (e.g. 1% -> 5% -> 10% -> 25% -> 50% -> 100%)."""

    __tablename__ = "progressive_rollout_stages"
    __table_args__ = (
        UniqueConstraint("rollout_id", "stage_number", name="uq_prog_rollout_stage_num"),
        Index("ix_prog_rollout_stages_rollout", "rollout_id", "stage_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False)
    target_percentage: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ProgressiveStageStatus] = mapped_column(
        str_enum(ProgressiveStageStatus, length=32),
        nullable=False,
        default=ProgressiveStageStatus.PENDING,
    )
    observation_started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    observation_ends_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    minimum_observation_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    maximum_observation_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    actual_sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    healthy_evaluations_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    evaluation_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decision: Mapped[RolloutDecisionType | None] = mapped_column(
        str_enum(RolloutDecisionType, length=40), nullable=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    rollout: Mapped[ProgressiveRollout] = relationship(back_populates="stages")
    approval: Mapped[Approval | None] = relationship()


class RolloutMetricSnapshot(Base):
    """Collected metric observation snapshot for a rollout stage."""

    __tablename__ = "rollout_metric_snapshots"
    __table_args__ = (
        Index("ix_rollout_metrics_rollout_created", "rollout_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollout_stages.id", ondelete="SET NULL"),
        nullable=True,
    )
    metric_key: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    numerator: Mapped[float | None] = mapped_column(Float, nullable=True)
    denominator: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="rate")
    window_start: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    data_source: Mapped[str] = mapped_column(String(64), nullable=False, default="experiment_events")
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="fresh")
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    rollout: Mapped[ProgressiveRollout] = relationship(back_populates="metric_snapshots")


class RolloutGuardrail(Base):
    """Configured safety guardrail rule for a progressive rollout."""

    __tablename__ = "rollout_guardrails"
    __table_args__ = (Index("ix_rollout_guardrails_rollout", "rollout_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_key: Mapped[str] = mapped_column(String(80), nullable=False)
    operator: Mapped[GuardrailOperator] = mapped_column(
        str_enum(GuardrailOperator, length=40), nullable=False
    )
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=32),
        nullable=False,
        default=RiskClassification.CRITICAL,
    )
    evaluation_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    consecutive_breach_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_breach_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action: Mapped[str] = mapped_column(String(64), nullable=False, default="pause_and_recommend_rollback")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    rollout: Mapped[ProgressiveRollout] = relationship(back_populates="guardrails")


class GuardrailEvaluation(Base):
    """Recorded evaluation result for a RolloutGuardrail."""

    __tablename__ = "guardrail_evaluations"
    __table_args__ = (
        Index("ix_guardrail_evals_rollout", "rollout_id", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollout_stages.id", ondelete="SET NULL"),
        nullable=True,
    )
    guardrail_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rollout_guardrails.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[GuardrailEvalStatus] = mapped_column(
        str_enum(GuardrailEvalStatus, length=32), nullable=False
    )
    severity: Mapped[RiskClassification] = mapped_column(
        str_enum(RiskClassification, length=32), nullable=False
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    evaluated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class RolloutDecision(Base):
    """Explainable rollout progression or safety decision."""

    __tablename__ = "rollout_decisions"
    __table_args__ = (
        Index("ix_rollout_decisions_rollout", "rollout_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollout_stages.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_type: Mapped[RolloutDecisionType] = mapped_column(
        str_enum(RolloutDecisionType, length=40), nullable=False
    )
    decision_summary: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    missing_evidence: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    recommended_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="deterministic_guardrail_engine"
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    rollout: Mapped[ProgressiveRollout] = relationship(back_populates="decisions")


class ProgressiveRolloutEvent(Base):
    """Chronological timeline event for a ProgressiveRollout."""

    __tablename__ = "progressive_rollout_events"
    __table_args__ = (
        Index("ix_prog_rollout_events_rollout", "rollout_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollout_stages.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        str_enum(ActorType, length=30), nullable=False, default=ActorType.SYSTEM
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    rollout: Mapped[ProgressiveRollout] = relationship(back_populates="events")


class RollbackOperation(Base):
    """Approval-bound rollback request, execution, and readback verification record."""

    __tablename__ = "rollback_operations"
    __table_args__ = (Index("ix_rollback_ops_rollout", "rollout_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("progressive_rollout_stages.id", ondelete="SET NULL"),
        nullable=True,
    )
    feature_flag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False
    )
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    requested_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    status: Mapped[RollbackOperationStatus] = mapped_column(
        str_enum(RollbackOperationStatus, length=32),
        nullable=False,
        default=RollbackOperationStatus.RECOMMENDED,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    executed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verification_result: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    rollout: Mapped[ProgressiveRollout] = relationship(back_populates="rollbacks")
