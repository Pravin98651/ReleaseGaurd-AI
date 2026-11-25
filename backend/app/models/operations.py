"""Persistent scheduling (ScheduledJob & JobExecution) ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    JobExecutionStatus,
    JobTriggerType,
    NotificationCategory,
    ScheduleFrequency,
    ScheduleJobType,
    str_enum,
)


def _default_retry_policy() -> dict[str, Any]:
    return {"max_retries": 2, "retry_delay_seconds": 30, "timeout_seconds": 120}


def _default_notification_pref() -> dict[str, Any]:
    return {"on_success": False, "on_failure": True}


class ScheduledJob(Base):
    """Persistent recurring or one-time automation schedule backed by PostgreSQL/SQLite."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        Index("ix_scheduled_jobs_enabled_next", "enabled", "next_run_at"),
        Index("ix_scheduled_jobs_type", "job_type"),
        Index("ix_scheduled_jobs_owner", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_type: Mapped[ScheduleJobType] = mapped_column(
        str_enum(ScheduleJobType, length=50), nullable=False
    )
    frequency: Mapped[ScheduleFrequency] = mapped_column(
        str_enum(ScheduleFrequency, length=50),
        nullable=False,
        default=ScheduleFrequency.DAILY,
    )
    cron_expression: Mapped[str | None] = mapped_column(String(120), nullable=True)
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    start_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    retry_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=_default_retry_policy
    )
    notification_preference: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=_default_notification_pref
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missed_runs_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    executions: Mapped[list[JobExecution]] = relationship(
        back_populates="scheduled_job",
        passive_deletes=True,
    )


class JobExecution(Base):
    """Immutable historical record of scheduled & manual job runs (preserved on schedule delete)."""

    __tablename__ = "job_executions"
    __table_args__ = (
        Index("ix_job_exec_schedule_created", "scheduled_job_id", "created_at"),
        Index("ix_job_exec_status_created", "execution_status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scheduled_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scheduled_jobs.id", ondelete="SET NULL"), nullable=True
    )
    schedule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_type: Mapped[ScheduleJobType] = mapped_column(
        str_enum(ScheduleJobType, length=50), nullable=False
    )
    execution_status: Mapped[JobExecutionStatus] = mapped_column(
        str_enum(JobExecutionStatus, length=50),
        nullable=False,
        default=JobExecutionStatus.PENDING,
    )
    trigger_type: Mapped[JobTriggerType] = mapped_column(
        str_enum(JobTriggerType, length=50),
        nullable=False,
        default=JobTriggerType.SCHEDULED,
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompat, nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    scheduled_job: Mapped[ScheduledJob | None] = relationship(back_populates="executions")


class Notification(Base):
    """Persistent operational alert & notification record across all ReleaseGuard subsystems."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),
        Index("ix_notifications_category_created", "category", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    category: Mapped[NotificationCategory] = mapped_column(
        str_enum(NotificationCategory, length=60), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_href: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class NotificationPreference(Base):
    """Per-user / workspace notification delivery preferences."""

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    severity_threshold: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    muted_categories: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )
