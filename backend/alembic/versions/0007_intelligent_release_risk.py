"""Add Intelligent Release Risk Engine tables.

Revision ID: 0007_intelligent_release_risk
Revises: 0006_agent_command_center
Create Date: 2026-11-25 22:38:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONBCompat, TZDateTime
from app.models.enums import (
    DataSufficiency,
    DimensionStatus,
    FlagSuitability,
    MitigationStatus,
    RiskAssessmentStatus,
    RiskClassification,
    str_enum,
)

revision: str = "0007_intelligent_release_risk"
down_revision: str | Sequence[str] | None = "0006_agent_command_center"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_engine_configurations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("dimension_weights", JSONBCompat, nullable=False),
        sa.Column("risk_thresholds", JSONBCompat, nullable=False),
        sa.Column("critical_override_rules", JSONBCompat, nullable=False),
        sa.Column("updated_by_id", sa.Uuid(as_uuid=True), nullable=True),
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
            ["updated_by_id"], ["users.id"], name="fk_risk_cfg_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_engine_configurations"),
        sa.UniqueConstraint("config_version", name="uq_risk_engine_config_version"),
    )

    op.create_table(
        "release_risk_assessments",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_key", sa.String(length=48), nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("pull_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("target_environment", sa.String(length=64), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("release_version", sa.Integer(), nullable=True),
        sa.Column("assessment_status", str_enum(RiskAssessmentStatus, length=32), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("risk_level", str_enum(RiskClassification, length=32), nullable=False),
        sa.Column("assessment_confidence", sa.Float(), nullable=True),
        sa.Column("data_completeness", sa.Float(), nullable=False),
        sa.Column("flag_suitability", str_enum(FlagSuitability, length=40), nullable=False),
        sa.Column("scoring_version", sa.Integer(), nullable=False),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("evaluated_dimensions", JSONBCompat, nullable=False),
        sa.Column("unavailable_dimensions", JSONBCompat, nullable=False),
        sa.Column("limitations", JSONBCompat, nullable=False),
        sa.Column("critical_overrides", JSONBCompat, nullable=False),
        sa.Column("evidence_summary", JSONBCompat, nullable=False),
        sa.Column("dependency_graph", JSONBCompat, nullable=False),
        sa.Column("ai_interpretation", sa.Text(), nullable=True),
        sa.Column("fallback_mode", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
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
            ["release_id"], ["releases.id"], name="fk_risk_assess_release", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["pull_request_id"],
            ["pull_requests.id"],
            name="fk_risk_assess_pr",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_risk_assess_repo",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            name="fk_risk_assess_exec",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name="fk_risk_assess_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_release_risk_assessments"),
        sa.UniqueConstraint("assessment_key", name="uq_risk_assessments_key"),
    )
    op.create_index(
        "ix_risk_assessments_release_created",
        "release_risk_assessments",
        ["release_id", "created_at"],
    )
    op.create_index(
        "ix_risk_assessments_pr_created",
        "release_risk_assessments",
        ["pull_request_id", "created_at"],
    )
    op.create_index(
        "ix_risk_assessments_level",
        "release_risk_assessments",
        ["risk_level", "assessment_status"],
    )

    op.create_table(
        "risk_dimension_results",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dimension_key", sa.String(length=64), nullable=False),
        sa.Column("dimension_name", sa.String(length=128), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("weighted_contribution", sa.Float(), nullable=True),
        sa.Column("severity", str_enum(RiskClassification, length=32), nullable=False),
        sa.Column("status", str_enum(DimensionStatus, length=32), nullable=False),
        sa.Column("data_sufficiency", str_enum(DataSufficiency, length=32), nullable=False),
        sa.Column("evidence_quality", sa.String(length=32), nullable=False),
        sa.Column("explanations", JSONBCompat, nullable=False),
        sa.Column("limitations", JSONBCompat, nullable=False),
        sa.Column("details_json", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["release_risk_assessments.id"],
            name="fk_risk_dim_assess",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_dimension_results"),
        sa.UniqueConstraint("assessment_id", "dimension_key", name="uq_risk_dim_assess_key"),
    )
    op.create_index("ix_risk_dim_assess", "risk_dimension_results", ["assessment_id"])

    op.create_table(
        "risk_findings",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dimension_key", sa.String(length=64), nullable=False),
        sa.Column("finding_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", str_enum(RiskClassification, length=32), nullable=False),
        sa.Column("is_ai_inferred", sa.Boolean(), nullable=False),
        sa.Column("affected_module", sa.String(length=255), nullable=True),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["release_risk_assessments.id"],
            name="fk_risk_finding_assess",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_findings"),
    )
    op.create_index(
        "ix_risk_findings_assess_dim", "risk_findings", ["assessment_id", "dimension_key"]
    )

    op.create_table(
        "risk_evidence",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("finding_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("structured_data", JSONBCompat, nullable=False),
        sa.Column(
            "collected_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["release_risk_assessments.id"],
            name="fk_risk_evidence_assess",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["risk_findings.id"],
            name="fk_risk_evidence_finding",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_evidence"),
    )
    op.create_index("ix_risk_evidence_assess", "risk_evidence", ["assessment_id"])

    op.create_table(
        "risk_mitigations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("finding_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("dimension_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", str_enum(RiskClassification, length=32), nullable=False),
        sa.Column("mitigation_type", sa.String(length=64), nullable=False),
        sa.Column("status", str_enum(MitigationStatus, length=32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("suggested_role", sa.String(length=64), nullable=True),
        sa.Column("verification_criteria", sa.Text(), nullable=True),
        sa.Column("verification_evidence", sa.Text(), nullable=True),
        sa.Column("accepted_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
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
            ["assessment_id"],
            ["release_risk_assessments.id"],
            name="fk_risk_mit_assess",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["risk_findings.id"],
            name="fk_risk_mit_finding",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_id"], ["users.id"], name="fk_risk_mit_acc_user", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_id"], ["users.id"], name="fk_risk_mit_comp_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_mitigations"),
    )
    op.create_index(
        "ix_risk_mitigations_assess_status", "risk_mitigations", ["assessment_id", "status"]
    )

    op.create_table(
        "risk_scenarios",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("trigger_condition", sa.Text(), nullable=False),
        sa.Column("affected_component", sa.String(length=255), nullable=False),
        sa.Column("potential_impact", sa.Text(), nullable=False),
        sa.Column("severity", str_enum(RiskClassification, length=32), nullable=False),
        sa.Column("likelihood", sa.String(length=32), nullable=True),
        sa.Column("is_potential", sa.Boolean(), nullable=False),
        sa.Column("supporting_evidence", JSONBCompat, nullable=False),
        sa.Column("detection_strategy", sa.Text(), nullable=True),
        sa.Column("mitigation_summary", sa.Text(), nullable=True),
        sa.Column("related_dimensions", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["release_risk_assessments.id"],
            name="fk_risk_scen_assess",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_scenarios"),
    )
    op.create_index("ix_risk_scenarios_assess", "risk_scenarios", ["assessment_id"])


def downgrade() -> None:
    op.drop_index("ix_risk_scenarios_assess", table_name="risk_scenarios")
    op.drop_table("risk_scenarios")
    op.drop_index("ix_risk_mitigations_assess_status", table_name="risk_mitigations")
    op.drop_table("risk_mitigations")
    op.drop_index("ix_risk_evidence_assess", table_name="risk_evidence")
    op.drop_table("risk_evidence")
    op.drop_index("ix_risk_findings_assess_dim", table_name="risk_findings")
    op.drop_table("risk_findings")
    op.drop_index("ix_risk_dim_assess", table_name="risk_dimension_results")
    op.drop_table("risk_dimension_results")
    op.drop_index("ix_risk_assessments_level", table_name="release_risk_assessments")
    op.drop_index("ix_risk_assessments_pr_created", table_name="release_risk_assessments")
    op.drop_index("ix_risk_assessments_release_created", table_name="release_risk_assessments")
    op.drop_table("release_risk_assessments")
    op.drop_table("risk_engine_configurations")
