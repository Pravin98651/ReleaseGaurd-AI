"""Add developer productivity benchmark tables (BenchmarkRun & BenchmarkStage)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_productivity_benchmarks"
down_revision: str | None = "0011_multi_repo_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONBCompat = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("benchmark_id", sa.String(length=120), nullable=False),
        sa.Column("workflow_type", sa.String(length=40), nullable=False),
        sa.Column("execution_id", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_duration_seconds", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("active_developer_seconds", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "unattended_execution_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("api_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("agent_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "human_intervention_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "human_intervention_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("approval_latency_seconds", sa.Float(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "environment",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'development'"),
        ),
        sa.Column(
            "scenario",
            sa.String(length=120),
            nullable=False,
            server_default=sa.text("'default'"),
        ),
        sa.Column(
            "evidence_classification",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'VERIFIED_MEASURED'"),
        ),
        sa.Column("manual_baseline_seconds", sa.Float(), nullable=True),
        sa.Column("manual_baseline_source", sa.Text(), nullable=True),
        sa.Column("impact_metrics", JSONBCompat, nullable=False),
        sa.Column("metadata_json", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_benchmark_runs_benchmark_id", "benchmark_runs", ["benchmark_id"], unique=True)
    op.create_index(
        "ix_benchmark_runs_workflow_created", "benchmark_runs", ["workflow_type", "created_at"]
    )
    op.create_index(
        "ix_benchmark_runs_env_created", "benchmark_runs", ["environment", "created_at"]
    )
    op.create_index("ix_benchmark_runs_evidence", "benchmark_runs", ["evidence_classification"])

    op.create_table(
        "benchmark_stages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "benchmark_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_order", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("stage_key", sa.String(length=80), nullable=False),
        sa.Column("stage_name", sa.String(length=160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "active_developer_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "unattended_execution_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("api_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("agent_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "human_intervention_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "evidence_classification",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'VERIFIED_MEASURED'"),
        ),
        sa.Column("manual_baseline_seconds", sa.Float(), nullable=True),
        sa.Column("details", JSONBCompat, nullable=False),
    )
    op.create_index(
        "ix_benchmark_stages_run_order", "benchmark_stages", ["benchmark_run_id", "stage_order"]
    )


def downgrade() -> None:
    op.drop_index("ix_benchmark_stages_run_order", table_name="benchmark_stages")
    op.drop_table("benchmark_stages")
    op.drop_index("ix_benchmark_runs_evidence", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_env_created", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_workflow_created", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_benchmark_id", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
