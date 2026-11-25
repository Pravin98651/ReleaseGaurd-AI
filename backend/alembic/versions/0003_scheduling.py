"""Add scheduled_jobs and job_executions tables for persistent automation scheduling.

Revision ID: 0003_scheduling
Revises: 0002_auth_rbac_fields
Create Date: 2026-11-25 20:12:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONBCompat, TZDateTime
from app.models.enums import (
    JobExecutionStatus,
    JobTriggerType,
    ScheduleFrequency,
    ScheduleJobType,
    str_enum,
)

revision: str = "0003_scheduling"
down_revision: str | Sequence[str] | None = "0002_auth_rbac_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("job_type", str_enum(ScheduleJobType, length=50), nullable=False),
        sa.Column("frequency", str_enum(ScheduleFrequency, length=50), nullable=False),
        sa.Column("cron_expression", sa.String(length=120), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("start_at", TZDateTime(), nullable=True),
        sa.Column("end_at", TZDateTime(), nullable=True),
        sa.Column("configuration", JSONBCompat, nullable=False),
        sa.Column("retry_policy", JSONBCompat, nullable=False),
        sa.Column("notification_preference", JSONBCompat, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("last_run_at", TZDateTime(), nullable=True),
        sa.Column("next_run_at", TZDateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=50), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("missed_runs_count", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("locked_until", TZDateTime(), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_scheduled_jobs_owner_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scheduled_jobs"),
    )
    op.create_index(
        "ix_scheduled_jobs_enabled_next",
        "scheduled_jobs",
        ["enabled", "next_run_at"],
        unique=False,
    )
    op.create_index("ix_scheduled_jobs_type", "scheduled_jobs", ["job_type"], unique=False)
    op.create_index("ix_scheduled_jobs_owner", "scheduled_jobs", ["owner_id"], unique=False)

    op.create_table(
        "job_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scheduled_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("schedule_name", sa.String(length=255), nullable=False),
        sa.Column("job_type", str_enum(ScheduleJobType, length=50), nullable=False),
        sa.Column("execution_status", str_enum(JobExecutionStatus, length=50), nullable=False),
        sa.Column("trigger_type", str_enum(JobTriggerType, length=50), nullable=False),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("input_payload", JSONBCompat, nullable=False),
        sa.Column("result_summary", JSONBCompat, nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("agent_task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_job_id"],
            ["scheduled_jobs.id"],
            name="fk_job_executions_scheduled_job_id_scheduled_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_task_id"],
            ["agent_tasks.id"],
            name="fk_job_executions_agent_task_id_agent_tasks",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_executions"),
    )
    op.create_index(
        "ix_job_exec_schedule_created",
        "job_executions",
        ["scheduled_job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_exec_status_created",
        "job_executions",
        ["execution_status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_exec_status_created", table_name="job_executions")
    op.drop_index("ix_job_exec_schedule_created", table_name="job_executions")
    op.drop_table("job_executions")
    op.drop_index("ix_scheduled_jobs_owner", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_type", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_enabled_next", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")
