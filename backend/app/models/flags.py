"""FeatureFlag and RolloutHistory ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from app.models.enums import FlagType, RolloutAction, str_enum

if TYPE_CHECKING:
    from app.models.experiments import Experiment
    from app.models.github import CodeChange, Repository
    from app.models.governance import Approval


class FeatureFlag(Base):
    """Synchronized Flagsmith feature flag projection and Flag Health assessment."""

    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint("flagsmith_project_id", "key", name="uq_flag_project_key"),
        Index("ix_flag_health_score", "health_score"),
        CheckConstraint(
            "health_score >= 0 AND health_score <= 100",
            name="health_score_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    flagsmith_feature_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flagsmith_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    flag_type: Mapped[FlagType] = mapped_column(
        str_enum(FlagType, length=50),
        nullable=False,
        default=FlagType.STANDARD,
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    env_states: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    mv_options: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    code_references: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    health_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    health_issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_health_scan_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    flagsmith_created_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    repository: Mapped[Repository | None] = relationship(back_populates="feature_flags")
    experiments: Mapped[list[Experiment]] = relationship(
        back_populates="feature_flag", cascade="all, delete-orphan"
    )
    rollout_history: Mapped[list[RolloutHistory]] = relationship(
        back_populates="feature_flag", cascade="all, delete-orphan"
    )
    code_changes: Mapped[list[CodeChange]] = relationship(back_populates="feature_flag")


class RolloutHistory(Base):
    """Immutable timeline of every flag toggle, gradual rollout step, and rollback."""

    __tablename__ = "rollout_history"
    __table_args__ = (
        Index("ix_rollout_flag_env_time", "feature_flag_id", "environment_name", "created_at"),
        Index("ix_rollout_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_flag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    environment_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[RolloutAction] = mapped_column(
        str_enum(RolloutAction, length=50), nullable=False
    )
    previous_state: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False)
    new_state: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_details: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    feature_flag: Mapped[FeatureFlag] = relationship(back_populates="rollout_history")
    approval: Mapped[Approval | None] = relationship(back_populates="rollout_history")
