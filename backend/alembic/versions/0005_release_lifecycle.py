"""Add release lifecycle tables and link approvals to releases.

Revision ID: 0005_release_lifecycle
Revises: 0004_notifications
Create Date: 2026-11-25 21:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONBCompat, TZDateTime
from app.models.enums import (
    ActorType,
    ReleaseArtifactType,
    ReleaseCheckStatus,
    ReleaseReadinessStatus,
    ReleaseStageKey,
    ReleaseStageStatus,
    ReleaseStatus,
    ReleaseType,
    RiskLevel,
    str_enum,
)

revision: str = "0005_release_lifecycle"
down_revision: str | Sequence[str] | None = "0004_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "releases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("release_key", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("release_type", str_enum(ReleaseType, length=32), nullable=False),
        sa.Column("target_environment", sa.String(length=64), nullable=False),
        sa.Column("status", str_enum(ReleaseStatus, length=40), nullable=False),
        sa.Column("previous_status", str_enum(ReleaseStatus, length=40), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("pull_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("feature_flag_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("experiment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("risk_level", str_enum(RiskLevel, length=20), nullable=False),
        sa.Column("readiness_score", sa.Integer(), nullable=False),
        sa.Column(
            "readiness_status", str_enum(ReleaseReadinessStatus, length=32), nullable=False
        ),
        sa.Column("readiness_summary", JSONBCompat, nullable=False),
        sa.Column("target_rollout_percentage", sa.Integer(), nullable=False),
        sa.Column("current_rollout_percentage", sa.Integer(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONBCompat, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("scheduled_for", TZDateTime(), nullable=True),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("paused_at", TZDateTime(), nullable=True),
        sa.Column("cancelled_at", TZDateTime(), nullable=True),
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
            ["repository_id"],
            ["repositories.id"],
            name="fk_releases_repository_id_repositories",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["pull_request_id"],
            ["pull_requests.id"],
            name="fk_releases_pull_request_id_pull_requests",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["feature_flag_id"],
            ["feature_flags.id"],
            name="fk_releases_feature_flag_id_feature_flags",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name="fk_releases_experiment_id_experiments",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_releases_owner_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_releases_created_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_releases"),
        sa.UniqueConstraint("release_key", name="uq_releases_release_key"),
    )
    op.create_index("ix_releases_status_env", "releases", ["status", "target_environment"])
    op.create_index("ix_releases_readiness", "releases", ["readiness_status", "readiness_score"])
    op.create_index("ix_releases_pr", "releases", ["pull_request_id"])
    op.create_index("ix_releases_repo_created", "releases", ["repository_id", "created_at"])

    op.create_table(
        "release_stages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_key", str_enum(ReleaseStageKey, length=40), nullable=False),
        sa.Column("stage_order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("status", str_enum(ReleaseStageStatus, length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details_json", JSONBCompat, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", TZDateTime(), nullable=True),
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
            ["release_id"],
            ["releases.id"],
            name="fk_release_stages_release_id_releases",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_release_stages"),
        sa.UniqueConstraint("release_id", "stage_key", name="uq_release_stages_release_key"),
    )
    op.create_index(
        "ix_release_stages_release_order", "release_stages", ["release_id", "stage_order"]
    )

    op.create_table(
        "release_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", str_enum(ReleaseStatus, length=40), nullable=True),
        sa.Column("to_status", str_enum(ReleaseStatus, length=40), nullable=True),
        sa.Column("stage_key", str_enum(ReleaseStageKey, length=40), nullable=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_type", str_enum(ActorType, length=30), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload_json", JSONBCompat, nullable=False),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["releases.id"],
            name="fk_release_events_release_id_releases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_release_events_actor_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_release_events"),
    )
    op.create_index(
        "ix_release_events_release_created", "release_events", ["release_id", "created_at"]
    )
    op.create_index("ix_release_events_type", "release_events", ["event_type"])

    op.create_table(
        "release_artifacts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("artifact_type", str_enum(ReleaseArtifactType, length=40), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("uri", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content_json", JSONBCompat, nullable=False),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["releases.id"],
            name="fk_release_artifacts_release_id_releases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_release_artifacts_created_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_release_artifacts"),
    )
    op.create_index(
        "ix_release_artifacts_release_type", "release_artifacts", ["release_id", "artifact_type"]
    )

    op.create_table(
        "release_checks",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("check_key", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", str_enum(ReleaseCheckStatus, length=32), nullable=False),
        sa.Column("is_blocking", sa.Boolean(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details_json", JSONBCompat, nullable=False),
        sa.Column("evaluated_at", TZDateTime(), nullable=True),
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
            ["release_id"],
            ["releases.id"],
            name="fk_release_checks_release_id_releases",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_release_checks"),
        sa.UniqueConstraint("release_id", "check_key", name="uq_release_checks_release_key"),
    )
    op.create_index("ix_release_checks_release_status", "release_checks", ["release_id", "status"])

    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(sa.Column("release_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("release_version", sa.Integer(), nullable=True))
        batch_op.create_index("ix_approvals_release", ["release_id"])
        batch_op.create_foreign_key(
            "fk_approvals_release_id_releases",
            "releases",
            ["release_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_constraint("fk_approvals_release_id_releases", type_="foreignkey")
        batch_op.drop_index("ix_approvals_release")
        batch_op.drop_column("release_version")
        batch_op.drop_column("release_id")

    op.drop_index("ix_release_checks_release_status", table_name="release_checks")
    op.drop_table("release_checks")
    op.drop_index("ix_release_artifacts_release_type", table_name="release_artifacts")
    op.drop_table("release_artifacts")
    op.drop_index("ix_release_events_type", table_name="release_events")
    op.drop_index("ix_release_events_release_created", table_name="release_events")
    op.drop_table("release_events")
    op.drop_index("ix_release_stages_release_order", table_name="release_stages")
    op.drop_table("release_stages")
    op.drop_index("ix_releases_repo_created", table_name="releases")
    op.drop_index("ix_releases_pr", table_name="releases")
    op.drop_index("ix_releases_readiness", table_name="releases")
    op.drop_index("ix_releases_status_env", table_name="releases")
    op.drop_table("releases")
