"""Approval and AuditLog governance ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CHAR, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    ActorType,
    ApprovalCategory,
    ApprovalStatus,
    AuditStatus,
    PolicyCategory,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.agents import AgentTask
    from app.models.flags import RolloutHistory
    from app.models.identity import User
    from app.models.releases import Release


class Approval(Base):
    """Single-use, SHA-256 hash-bound approval requests governing Category B & C actions."""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_status_expires", "status", "expires_at"),
        Index("ix_approvals_action_hash", "action_hash"),
        Index("ix_approvals_task", "agent_task_id"),
        Index("ix_approvals_release", "release_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    release_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    policy_category: Mapped[ApprovalCategory] = mapped_column(
        str_enum(ApprovalCategory, length=10), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_payload: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False)
    expected_precondition: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    action_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        str_enum(ApprovalStatus, length=50),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompat, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    agent_task: Mapped[AgentTask | None] = relationship(back_populates="approvals")
    release: Mapped[Release | None] = relationship(back_populates="approvals")
    requested_by: Mapped[User | None] = relationship(
        foreign_keys=[requested_by_id], back_populates="requested_approvals"
    )
    decided_by: Mapped[User | None] = relationship(
        foreign_keys=[decided_by_id], back_populates="decided_approvals"
    )
    rollout_history: Mapped[list[RolloutHistory]] = relationship(back_populates="approval")
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="approval")


class AuditLog(Base):
    """Append-only compliance log recording policy decisions, approvals, and mutations."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_created", "created_at"),
        Index("ix_audit_category_action", "policy_category", "event_action"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_type: Mapped[ActorType] = mapped_column(
        str_enum(ActorType, length=50), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_category: Mapped[PolicyCategory] = mapped_column(
        str_enum(PolicyCategory, length=10), nullable=False
    )
    event_action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    action_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    status: Mapped[AuditStatus] = mapped_column(
        str_enum(AuditStatus, length=50), nullable=False
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    approval: Mapped[Approval | None] = relationship(back_populates="audit_logs")
