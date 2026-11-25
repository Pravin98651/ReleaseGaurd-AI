"""Add Release Intelligence & Learning Engine tables (Phase 5)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_release_intelligence"
down_revision: str | None = "0008_progressive_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONBCompat = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "release_outcomes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), sa.ForeignKey("releases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("outcome_type", sa.String(length=40), nullable=False),
        sa.Column("failure_category", sa.String(length=50), nullable=True),
        sa.Column("contributing_categories", JSONBCompat, nullable=False),
        sa.Column("final_status", sa.String(length=40), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("release_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("recovery_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("lead_time_seconds", sa.Integer(), nullable=True),
        sa.Column("approval_latency_seconds", sa.Integer(), nullable=True),
        sa.Column("rollback_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("evidence_classification", sa.String(length=32), nullable=False),
        sa.Column("evidence", JSONBCompat, nullable=False),
        sa.Column("data_quality_flags", JSONBCompat, nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_release_outcomes_release_unique", "release_outcomes", ["release_id"], unique=True)
    op.create_index("ix_release_outcomes_env_type", "release_outcomes", ["environment", "outcome_type"])
    op.create_index("ix_release_outcomes_recorded", "release_outcomes", ["recorded_at"])

    op.create_table(
        "release_failure_patterns",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("pattern_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("affected_module", sa.String(length=255), nullable=True),
        sa.Column("dependency_reference", sa.String(length=255), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_quality", sa.String(length=32), nullable=False),
        sa.Column("is_exploratory", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("confidence_data", JSONBCompat, nullable=False),
        sa.Column("supporting_release_ids", JSONBCompat, nullable=False),
        sa.Column("recommended_mitigation", sa.Text(), nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_failure_patterns_key_unique", "release_failure_patterns", ["pattern_key"], unique=True)
    op.create_index("ix_failure_patterns_category", "release_failure_patterns", ["category"])
    op.create_index("ix_failure_patterns_repo", "release_failure_patterns", ["repository_id"])

    op.create_table(
        "repository_intelligence_profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("release_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rollback_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("historical_failure_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_recovery_seconds", sa.Float(), nullable=True),
        sa.Column("common_failure_categories", JSONBCompat, nullable=False),
        sa.Column("high_risk_modules", JSONBCompat, nullable=False),
        sa.Column("dependency_patterns", JSONBCompat, nullable=False),
        sa.Column("risk_indicators", JSONBCompat, nullable=False),
        sa.Column("recommendations", JSONBCompat, nullable=False),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_repo_intel_repo_unique", "repository_intelligence_profiles", ["repository_id"], unique=True)

    op.create_table(
        "recommendation_outcomes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("recommendation_id", sa.Uuid(as_uuid=True), sa.ForeignKey("agent_decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), sa.ForeignKey("releases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recommendation_category", sa.String(length=80), nullable=False),
        sa.Column("outcome_classification", sa.String(length=40), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("implemented", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("actual_outcome", sa.Text(), nullable=True),
        sa.Column("evidence", JSONBCompat, nullable=False),
        sa.Column("evaluation_method", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rec_outcomes_decision_unique", "recommendation_outcomes", ["recommendation_id"], unique=True)
    op.create_index("ix_rec_outcomes_class", "recommendation_outcomes", ["outcome_classification"])

    op.create_table(
        "release_insights",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("insight_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("insight_category", sa.String(length=64), nullable=False),
        sa.Column("evidence", JSONBCompat, nullable=False),
        sa.Column("evidence_classification", sa.String(length=32), nullable=False),
        sa.Column("affected_repositories", JSONBCompat, nullable=False),
        sa.Column("affected_releases", JSONBCompat, nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_data", JSONBCompat, nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("generated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_release_insights_key_unique", "release_insights", ["insight_key"], unique=True)
    op.create_index("ix_release_insights_category", "release_insights", ["insight_category"])

    op.create_table(
        "policy_recommendations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("policy_area", sa.String(length=50), nullable=False),
        sa.Column("current_configuration", JSONBCompat, nullable=False),
        sa.Column("proposed_configuration", JSONBCompat, nullable=False),
        sa.Column("approved_configuration", JSONBCompat, nullable=True),
        sa.Column("supporting_evidence", JSONBCompat, nullable=False),
        sa.Column("affected_repositories", JSONBCompat, nullable=False),
        sa.Column("expected_benefit", sa.Text(), nullable=True),
        sa.Column("tradeoffs", sa.Text(), nullable=True),
        sa.Column("confidence_data", JSONBCompat, nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), sa.ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("implemented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_evidence", JSONBCompat, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_policy_recs_status", "policy_recommendations", ["review_status"])
    op.create_index("ix_policy_recs_area", "policy_recommendations", ["policy_area"])

    op.create_table(
        "intelligence_analysis_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("analysis_type", sa.String(length=64), nullable=False),
        sa.Column("scope", JSONBCompat, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", JSONBCompat, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("analysis_version", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_intel_runs_type_started", "intelligence_analysis_runs", ["analysis_type", "started_at"])


def downgrade() -> None:
    op.drop_table("intelligence_analysis_runs")
    op.drop_table("policy_recommendations")
    op.drop_table("release_insights")
    op.drop_table("recommendation_outcomes")
    op.drop_table("repository_intelligence_profiles")
    op.drop_table("release_failure_patterns")
    op.drop_table("release_outcomes")
