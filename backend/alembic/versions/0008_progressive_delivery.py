"""Add Progressive Delivery Engine tables.

Revision ID: 0008_progressive_delivery
Revises: 0007_intelligent_release_risk
Create Date: 2026-11-25 22:48:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONBCompat, TZDateTime
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

revision: str = "0008_progressive_delivery"
down_revision: str | Sequence[str] | None = "0007_intelligent_release_risk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "progressive_rollouts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_key", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("pull_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("feature_flag_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("feature_flag_key", sa.String(length=128), nullable=False),
        sa.Column("experiment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("status", str_enum(ProgressiveRolloutStatus, length=40), nullable=False),
        sa.Column("previous_status", str_enum(ProgressiveRolloutStatus, length=40), nullable=True),
        sa.Column("current_stage_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("current_percentage", sa.Integer(), nullable=False),
        sa.Column("target_percentage", sa.Integer(), nullable=False),
        sa.Column("primary_metric_key", sa.String(length=64), nullable=False),
        sa.Column("rollout_configuration", JSONBCompat, nullable=False),
        sa.Column("observation_configuration", JSONBCompat, nullable=False),
        sa.Column("rollback_configuration", JSONBCompat, nullable=False),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
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
            ["release_id"], ["releases.id"], name="fk_prog_rollout_release", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], name="fk_prog_rollout_repo", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["pull_request_id"], ["pull_requests.id"], name="fk_prog_rollout_pr", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["feature_flag_id"],
            ["feature_flags.id"],
            name="fk_prog_rollout_flag",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.id"], name="fk_prog_rollout_exp", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name="fk_prog_rollout_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_progressive_rollouts"),
        sa.UniqueConstraint("rollout_key", name="uq_progressive_rollouts_key"),
    )
    op.create_index("ix_prog_rollouts_status_env", "progressive_rollouts", ["status", "environment"])
    op.create_index(
        "ix_prog_rollouts_flag_env", "progressive_rollouts", ["feature_flag_id", "environment"]
    )
    op.create_index("ix_prog_rollouts_release", "progressive_rollouts", ["release_id"])

    op.create_table(
        "progressive_rollout_stages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_number", sa.Integer(), nullable=False),
        sa.Column("target_percentage", sa.Integer(), nullable=False),
        sa.Column("status", str_enum(ProgressiveStageStatus, length=32), nullable=False),
        sa.Column("observation_started_at", TZDateTime(), nullable=True),
        sa.Column("observation_ends_at", TZDateTime(), nullable=True),
        sa.Column("minimum_observation_seconds", sa.Integer(), nullable=False),
        sa.Column("maximum_observation_seconds", sa.Integer(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("actual_sample_size", sa.Integer(), nullable=False),
        sa.Column("healthy_evaluations_count", sa.Integer(), nullable=False),
        sa.Column("activated_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("evaluation_status", sa.String(length=40), nullable=True),
        sa.Column("decision", str_enum(RolloutDecisionType, length=40), nullable=True),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), nullable=True),
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
            ["rollout_id"],
            ["progressive_rollouts.id"],
            name="fk_prog_stage_rollout",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name="fk_prog_stage_approval", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_progressive_rollout_stages"),
        sa.UniqueConstraint("rollout_id", "stage_number", name="uq_prog_rollout_stage_num"),
    )
    op.create_index(
        "ix_prog_rollout_stages_rollout",
        "progressive_rollout_stages",
        ["rollout_id", "stage_number"],
    )

    op.create_table(
        "rollout_metric_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("metric_key", sa.String(length=80), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("numerator", sa.Float(), nullable=True),
        sa.Column("denominator", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("window_start", TZDateTime(), nullable=False),
        sa.Column("window_end", TZDateTime(), nullable=False),
        sa.Column("data_source", sa.String(length=64), nullable=False),
        sa.Column("freshness_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"],
            ["progressive_rollouts.id"],
            name="fk_rollout_metric_rollout",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["progressive_rollout_stages.id"],
            name="fk_rollout_metric_stage",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rollout_metric_snapshots"),
    )
    op.create_index(
        "ix_rollout_metrics_rollout_created",
        "rollout_metric_snapshots",
        ["rollout_id", "created_at"],
    )

    op.create_table(
        "rollout_guardrails",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric_key", sa.String(length=80), nullable=False),
        sa.Column("operator", str_enum(GuardrailOperator, length=40), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("severity", str_enum(RiskClassification, length=32), nullable=False),
        sa.Column("evaluation_window_seconds", sa.Integer(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("consecutive_breach_count", sa.Integer(), nullable=False),
        sa.Column("current_breach_streak", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
            ["rollout_id"],
            ["progressive_rollouts.id"],
            name="fk_rollout_guardrail_rollout",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rollout_guardrails"),
    )
    op.create_index("ix_rollout_guardrails_rollout", "rollout_guardrails", ["rollout_id"])

    op.create_table(
        "guardrail_evaluations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("guardrail_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("status", str_enum(GuardrailEvalStatus, length=32), nullable=False),
        sa.Column("severity", str_enum(RiskClassification, length=32), nullable=False),
        sa.Column("evidence", JSONBCompat, nullable=False),
        sa.Column(
            "evaluated_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"],
            ["progressive_rollouts.id"],
            name="fk_guardrail_eval_rollout",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["progressive_rollout_stages.id"],
            name="fk_guardrail_eval_stage",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["guardrail_id"],
            ["rollout_guardrails.id"],
            name="fk_guardrail_eval_guardrail",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_evaluations"),
    )
    op.create_index(
        "ix_guardrail_evals_rollout", "guardrail_evaluations", ["rollout_id", "evaluated_at"]
    )

    op.create_table(
        "rollout_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decision_type", str_enum(RolloutDecisionType, length=40), nullable=False),
        sa.Column("decision_summary", sa.Text(), nullable=False),
        sa.Column("supporting_evidence", JSONBCompat, nullable=False),
        sa.Column("missing_evidence", JSONBCompat, nullable=False),
        sa.Column("recommended_action", sa.String(length=255), nullable=True),
        sa.Column("decision_source", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"],
            ["progressive_rollouts.id"],
            name="fk_rollout_dec_rollout",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["progressive_rollout_stages.id"],
            name="fk_rollout_dec_stage",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rollout_decisions"),
    )
    op.create_index("ix_rollout_decisions_rollout", "rollout_decisions", ["rollout_id", "created_at"])

    op.create_table(
        "progressive_rollout_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("actor_type", str_enum(ActorType, length=30), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("structured_data", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"],
            ["progressive_rollouts.id"],
            name="fk_prog_event_rollout",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["progressive_rollout_stages.id"],
            name="fk_prog_event_stage",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_prog_event_actor", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_progressive_rollout_events"),
    )
    op.create_index(
        "ix_prog_rollout_events_rollout",
        "progressive_rollout_events",
        ["rollout_id", "created_at"],
    )

    op.create_table(
        "rollback_operations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rollout_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("feature_flag_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("previous_configuration", JSONBCompat, nullable=False),
        sa.Column("requested_configuration", JSONBCompat, nullable=False),
        sa.Column("status", str_enum(RollbackOperationStatus, length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("executed_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("verification_result", JSONBCompat, nullable=False),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rollout_id"],
            ["progressive_rollouts.id"],
            name="fk_rollback_op_rollout",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"],
            ["progressive_rollout_stages.id"],
            name="fk_rollback_op_stage",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["feature_flag_id"],
            ["feature_flags.id"],
            name="fk_rollback_op_flag",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name="fk_rollback_op_approval", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"], name="fk_rollback_op_req_user", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["executed_by_id"], ["users.id"], name="fk_rollback_op_exec_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rollback_operations"),
    )
    op.create_index("ix_rollback_ops_rollout", "rollback_operations", ["rollout_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_rollback_ops_rollout", table_name="rollback_operations")
    op.drop_table("rollback_operations")
    op.drop_index("ix_prog_rollout_events_rollout", table_name="progressive_rollout_events")
    op.drop_table("progressive_rollout_events")
    op.drop_index("ix_rollout_decisions_rollout", table_name="rollout_decisions")
    op.drop_table("rollout_decisions")
    op.drop_index("ix_guardrail_evals_rollout", table_name="guardrail_evaluations")
    op.drop_table("guardrail_evaluations")
    op.drop_index("ix_rollout_guardrails_rollout", table_name="rollout_guardrails")
    op.drop_table("rollout_guardrails")
    op.drop_index("ix_rollout_metrics_rollout_created", table_name="rollout_metric_snapshots")
    op.drop_table("rollout_metric_snapshots")
    op.drop_index("ix_prog_rollout_stages_rollout", table_name="progressive_rollout_stages")
    op.drop_table("progressive_rollout_stages")
    op.drop_index("ix_prog_rollouts_release", table_name="progressive_rollouts")
    op.drop_index("ix_prog_rollouts_flag_env", table_name="progressive_rollouts")
    op.drop_index("ix_prog_rollouts_status_env", table_name="progressive_rollouts")
    op.drop_table("progressive_rollouts")
