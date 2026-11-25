"""Add Multi-Repository & Organization Release Intelligence tables (Phase 7)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_multi_repo_intelligence"
down_revision: str | None = "0010_dx_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONBCompat = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("github_org_id", sa.Integer(), nullable=True),
        sa.Column("settings", JSONBCompat, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_organizations_slug_unique", "organizations", ["slug"], unique=True)

    op.create_table(
        "organization_repositories",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("github_repository_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_org_repos_org_repo_unique", "organization_repositories", ["organization_id", "repository_id"], unique=True)

    op.create_table(
        "repository_dependencies",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dependency_type", sa.String(length=40), nullable=False),
        sa.Column("version_constraint", sa.String(length=80), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("evidence_source", sa.String(length=120), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_repo_deps_src_tgt_type_unique", "repository_dependencies", ["source_repository_id", "target_repository_id", "dependency_type"], unique=True)
    op.create_index("ix_repo_deps_org", "repository_dependencies", ["organization_id"])

    op.create_table(
        "coordinated_release_plans",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk_summary", JSONBCompat, nullable=False),
        sa.Column("recommended_order", JSONBCompat, nullable=False),
        sa.Column("blocking_dependencies", JSONBCompat, nullable=False),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approval_id", sa.Uuid(as_uuid=True), sa.ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_coord_plans_org_status", "coordinated_release_plans", ["organization_id", "status"])

    op.create_table(
        "coordinated_release_items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("coordinated_plan_id", sa.Uuid(as_uuid=True), sa.ForeignKey("coordinated_release_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), sa.ForeignKey("releases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dependency_status", sa.String(length=32), nullable=False),
        sa.Column("coordination_status", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_coord_items_plan_order", "coordinated_release_items", ["coordinated_plan_id", "execution_order"])

    op.create_table(
        "organization_release_policies",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("policy_type", sa.String(length=64), nullable=False),
        sa.Column("configuration", JSONBCompat, nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_org_policies_org_status", "organization_release_policies", ["organization_id", "status"])

    op.create_table(
        "repository_policy_assignments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organization_release_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("override_configuration", JSONBCompat, nullable=True),
        sa.Column("exception_status", sa.String(length=32), nullable=False),
        sa.Column("approved_by_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_repo_policy_assign_unique", "repository_policy_assignments", ["repository_id", "policy_id"], unique=True)

    op.create_table(
        "organization_risk_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analysis_period", sa.String(length=32), nullable=False),
        sa.Column("repository_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("release_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rollback_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_summary", JSONBCompat, nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_org_risk_snap_org_calc", "organization_risk_snapshots", ["organization_id", "calculated_at"])

    op.create_table(
        "repository_health_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("health_category", sa.String(length=32), nullable=False),
        sa.Column("evidence", JSONBCompat, nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_completeness", sa.Float(), nullable=False, server_default="0"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_repo_health_snap_repo_calc", "repository_health_snapshots", ["repository_id", "calculated_at"])

    op.create_table(
        "cross_repository_impact_assessments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source_release_id", sa.Uuid(as_uuid=True), sa.ForeignKey("releases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("affected_repositories", JSONBCompat, nullable=False),
        sa.Column("dependency_evidence", JSONBCompat, nullable=False),
        sa.Column("risk_findings", JSONBCompat, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cross_impact_release", "cross_repository_impact_assessments", ["source_release_id"])


def downgrade() -> None:
    op.drop_table("cross_repository_impact_assessments")
    op.drop_table("repository_health_snapshots")
    op.drop_table("organization_risk_snapshots")
    op.drop_table("repository_policy_assignments")
    op.drop_table("organization_release_policies")
    op.drop_table("coordinated_release_items")
    op.drop_table("coordinated_release_plans")
    op.drop_table("repository_dependencies")
    op.drop_table("organization_repositories")
    op.drop_table("organizations")
