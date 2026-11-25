"""Add Agent Command Center execution, steps, tool calls, decisions, traces, and snapshots.

Revision ID: 0006_agent_command_center
Revises: 0005_release_lifecycle
Create Date: 2026-11-25 22:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONBCompat, TZDateTime
from app.models.enums import (
    ActorType,
    AgentDecisionSource,
    AgentDecisionStatus,
    AgentExecutionStatus,
    AgentStepStatus,
    AgentToolCallStatus,
    str_enum,
)

revision: str = "0006_agent_command_center"
down_revision: str | Sequence[str] | None = "0005_release_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_key", sa.String(length=48), nullable=False),
        sa.Column("agent_type", sa.String(length=64), nullable=False),
        sa.Column("agent_name", sa.String(length=128), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("task_description", sa.Text(), nullable=True),
        sa.Column("status", str_enum(AgentExecutionStatus, length=40), nullable=False),
        sa.Column("previous_status", str_enum(AgentExecutionStatus, length=40), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=False),
        sa.Column("initiated_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_type", str_enum(ActorType, length=30), nullable=False),
        sa.Column("agent_task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("pull_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("experiment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("schedule_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("parent_execution_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("current_stage", sa.String(length=128), nullable=True),
        sa.Column("current_activity", sa.String(length=255), nullable=True),
        sa.Column("completed_steps", sa.Integer(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=True),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("last_heartbeat_at", TZDateTime(), nullable=True),
        sa.Column("timeout_at", TZDateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("execution_metadata", JSONBCompat, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
            ["initiated_by_id"], ["users.id"], name="fk_agent_exec_initiated_by", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["agent_task_id"], ["agent_tasks.id"], name="fk_agent_exec_task", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], name="fk_agent_exec_repo", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["pull_request_id"], ["pull_requests.id"], name="fk_agent_exec_pr", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["release_id"], ["releases.id"], name="fk_agent_exec_release", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.id"], name="fk_agent_exec_exp", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["scheduled_jobs.id"], name="fk_agent_exec_sched", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_execution_id"],
            ["agent_executions.id"],
            name="fk_agent_exec_parent",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_executions"),
        sa.UniqueConstraint("execution_key", name="uq_agent_executions_execution_key"),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_executions_idempotency_key"),
    )
    op.create_index("ix_agent_executions_status_updated", "agent_executions", ["status", "updated_at"])
    op.create_index("ix_agent_executions_release", "agent_executions", ["release_id"])
    op.create_index("ix_agent_executions_repo", "agent_executions", ["repository_id"])
    op.create_index("ix_agent_executions_task", "agent_executions", ["agent_task_id"])
    op.create_index("ix_agent_executions_corr", "agent_executions", ["correlation_id"])

    op.create_table(
        "agent_execution_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("step_key", sa.String(length=80), nullable=False),
        sa.Column("step_name", sa.String(length=160), nullable=False),
        sa.Column("step_type", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", str_enum(AgentStepStatus, length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_summary", JSONBCompat, nullable=False),
        sa.Column("output_summary", JSONBCompat, nullable=False),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["agent_executions.id"], name="fk_agent_exec_steps_exec", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_execution_steps"),
        sa.UniqueConstraint("execution_id", "step_key", name="uq_agent_exec_steps_key"),
    )
    op.create_index("ix_agent_exec_steps_seq", "agent_execution_steps", ["execution_id", "sequence"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("step_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("tool_category", sa.String(length=64), nullable=False),
        sa.Column("invocation_id", sa.String(length=64), nullable=False),
        sa.Column("status", str_enum(AgentToolCallStatus, length=32), nullable=False),
        sa.Column("is_reversible", sa.Boolean(), nullable=False),
        sa.Column("input_summary", JSONBCompat, nullable=False),
        sa.Column("output_summary", JSONBCompat, nullable=False),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["agent_executions.id"], name="fk_agent_tool_calls_exec", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["step_id"], ["agent_execution_steps.id"], name="fk_agent_tool_calls_step", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_tool_calls"),
    )
    op.create_index("ix_agent_tool_calls_exec_created", "agent_tool_calls", ["execution_id", "created_at"])
    op.create_index("ix_agent_tool_calls_status", "agent_tool_calls", ["status"])

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("step_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decision_type", sa.String(length=80), nullable=False),
        sa.Column("decision_source", str_enum(AgentDecisionSource, length=40), nullable=False),
        sa.Column("decision_summary", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("decision_status", str_enum(AgentDecisionStatus, length=32), nullable=False),
        sa.Column("supporting_evidence", JSONBCompat, nullable=False),
        sa.Column("evidence_references", JSONBCompat, nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["agent_executions.id"], name="fk_agent_decisions_exec", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["step_id"], ["agent_execution_steps.id"], name="fk_agent_decisions_step", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_decisions"),
    )
    op.create_index("ix_agent_decisions_exec_created", "agent_decisions", ["execution_id", "created_at"])

    op.create_table(
        "agent_execution_traces",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=128), nullable=True),
        sa.Column("actor_type", str_enum(ActorType, length=30), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", JSONBCompat, nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["agent_executions.id"], name="fk_agent_exec_traces_exec", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_agent_exec_traces_actor", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_execution_traces"),
        sa.UniqueConstraint("execution_id", "sequence_number", name="uq_agent_exec_traces_seq"),
    )
    op.create_index(
        "ix_agent_exec_traces_exec_seq", "agent_execution_traces", ["execution_id", "sequence_number"]
    )
    op.create_index("ix_agent_exec_traces_type", "agent_execution_traces", ["event_type"])

    op.create_table(
        "agent_execution_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("checkpoint_number", sa.Integer(), nullable=False),
        sa.Column("checkpoint_type", sa.String(length=48), nullable=False),
        sa.Column("resumable_state", str_enum(AgentExecutionStatus, length=40), nullable=False),
        sa.Column("current_step_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_step_ids", JSONBCompat, nullable=False),
        sa.Column("pending_operation", sa.String(length=160), nullable=True),
        sa.Column("context_reference", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["agent_executions.id"], name="fk_agent_exec_snap_exec", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["current_step_id"], ["agent_execution_steps.id"], name="fk_agent_exec_snap_step", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_execution_snapshots"),
        sa.UniqueConstraint("execution_id", "checkpoint_number", name="uq_agent_exec_snap_num"),
    )
    op.create_index(
        "ix_agent_exec_snap_exec", "agent_execution_snapshots", ["execution_id", "checkpoint_number"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_exec_snap_exec", table_name="agent_execution_snapshots")
    op.drop_table("agent_execution_snapshots")
    op.drop_index("ix_agent_exec_traces_type", table_name="agent_execution_traces")
    op.drop_index("ix_agent_exec_traces_exec_seq", table_name="agent_execution_traces")
    op.drop_table("agent_execution_traces")
    op.drop_index("ix_agent_decisions_exec_created", table_name="agent_decisions")
    op.drop_table("agent_decisions")
    op.drop_index("ix_agent_tool_calls_status", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_exec_created", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_exec_steps_seq", table_name="agent_execution_steps")
    op.drop_table("agent_execution_steps")
    op.drop_index("ix_agent_executions_corr", table_name="agent_executions")
    op.drop_index("ix_agent_executions_task", table_name="agent_executions")
    op.drop_index("ix_agent_executions_repo", table_name="agent_executions")
    op.drop_index("ix_agent_executions_release", table_name="agent_executions")
    op.drop_index("ix_agent_executions_status_updated", table_name="agent_executions")
    op.drop_table("agent_executions")
