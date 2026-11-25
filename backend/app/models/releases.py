"""Release Lifecycle ORM models: Release, ReleaseStage, ReleaseEvent, ReleaseArtifact, ReleaseCheck."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
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
    ReleaseArtifactType,
    ReleaseCheckStatus,
    ReleaseReadinessStatus,
    ReleaseStageKey,
    ReleaseStageStatus,
    ReleaseStatus,
    ReleaseType,
    RiskLevel,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.experiments import Experiment
    from app.models.flags import FeatureFlag
    from app.models.github import PullRequest, Repository
    from app.models.governance import Approval
    from app.models.identity import User


class Release(Base):
    """Persistent end-to-end release lifecycle record unifying PRs, flags, experiments, and approvals."""

    __tablename__ = "releases"
    __table_args__ = (
        Index("ix_releases_status_env", "status", "target_environment"),
        Index("ix_releases_readiness", "readiness_status", "readiness_score"),
        Index("ix_releases_pr", "pull_request_id"),
        Index("ix_releases_repo_created", "repository_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_type: Mapped[ReleaseType] = mapped_column(
        str_enum(ReleaseType, length=32), nullable=False, default=ReleaseType.FEATURE
    )
    target_environment: Mapped[str] = mapped_column(
        String(64), nullable=False, default="development"
    )
    status: Mapped[ReleaseStatus] = mapped_column(
        str_enum(ReleaseStatus, length=40), nullable=False, default=ReleaseStatus.DRAFT
    )
    previous_status: Mapped[ReleaseStatus | None] = mapped_column(
        str_enum(ReleaseStatus, length=40), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    pull_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pull_requests.id", ondelete="SET NULL"), nullable=True
    )
    feature_flag_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("feature_flags.id", ondelete="SET NULL"), nullable=True
    )
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    risk_level: Mapped[RiskLevel] = mapped_column(
        str_enum(RiskLevel, length=20), nullable=False, default=RiskLevel.MEDIUM
    )
    readiness_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    readiness_status: Mapped[ReleaseReadinessStatus] = mapped_column(
        str_enum(ReleaseReadinessStatus, length=32),
        nullable=False,
        default=ReleaseReadinessStatus.NOT_READY,
    )
    readiness_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )

    target_rollout_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    current_rollout_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    scheduled_for: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    repository: Mapped[Repository | None] = relationship()
    pull_request: Mapped[PullRequest | None] = relationship()
    feature_flag: Mapped[FeatureFlag | None] = relationship()
    experiment: Mapped[Experiment | None] = relationship()
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])

    stages: Mapped[list[ReleaseStage]] = relationship(
        back_populates="release",
        cascade="all, delete-orphan",
        order_by="ReleaseStage.stage_order",
    )
    events: Mapped[list[ReleaseEvent]] = relationship(
        back_populates="release",
        cascade="all, delete-orphan",
        order_by="ReleaseEvent.created_at",
    )
    artifacts: Mapped[list[ReleaseArtifact]] = relationship(
        back_populates="release",
        cascade="all, delete-orphan",
        order_by="ReleaseArtifact.created_at",
    )
    checks: Mapped[list[ReleaseCheck]] = relationship(
        back_populates="release",
        cascade="all, delete-orphan",
    )
    approvals: Mapped[list[Approval]] = relationship(back_populates="release")


class ReleaseStage(Base):
    """Ordered stage record inside a release lifecycle."""

    __tablename__ = "release_stages"
    __table_args__ = (
        UniqueConstraint("release_id", "stage_key", name="uq_release_stages_release_key"),
        Index("ix_release_stages_release_order", "release_id", "stage_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    stage_key: Mapped[ReleaseStageKey] = mapped_column(
        str_enum(ReleaseStageKey, length=40), nullable=False
    )
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[ReleaseStageStatus] = mapped_column(
        str_enum(ReleaseStageStatus, length=32), nullable=False, default=ReleaseStageStatus.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    release: Mapped[Release] = relationship(back_populates="stages")


class ReleaseEvent(Base):
    """Immutable chronological timeline event for a release lifecycle."""

    __tablename__ = "release_events"
    __table_args__ = (
        Index("ix_release_events_release_created", "release_id", "created_at"),
        Index("ix_release_events_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_status: Mapped[ReleaseStatus | None] = mapped_column(
        str_enum(ReleaseStatus, length=40), nullable=True
    )
    to_status: Mapped[ReleaseStatus | None] = mapped_column(
        str_enum(ReleaseStatus, length=40), nullable=True
    )
    stage_key: Mapped[ReleaseStageKey | None] = mapped_column(
        str_enum(ReleaseStageKey, length=40), nullable=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_type: Mapped[ActorType] = mapped_column(
        str_enum(ActorType, length=30), nullable=False, default=ActorType.SYSTEM
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    release: Mapped[Release] = relationship(back_populates="events")
    actor: Mapped[User | None] = relationship()


class ReleaseArtifact(Base):
    """Evidence artifact (analysis, patch, validation, flag config, experiment, report) linked to a release."""

    __tablename__ = "release_artifacts"
    __table_args__ = (
        Index("ix_release_artifacts_release_type", "release_id", "artifact_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[ReleaseArtifactType] = mapped_column(
        str_enum(ReleaseArtifactType, length=40), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_table: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    release: Mapped[Release] = relationship(back_populates="artifacts")
    created_by: Mapped[User | None] = relationship()


class ReleaseCheck(Base):
    """Deterministic readiness verification check for a release."""

    __tablename__ = "release_checks"
    __table_args__ = (
        UniqueConstraint("release_id", "check_key", name="uq_release_checks_release_key"),
        Index("ix_release_checks_release_status", "release_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    check_key: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ReleaseCheckStatus] = mapped_column(
        str_enum(ReleaseCheckStatus, length=32), nullable=False, default=ReleaseCheckStatus.PENDING
    )
    is_blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    evaluated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    release: Mapped[Release] = relationship(back_populates="checks")
