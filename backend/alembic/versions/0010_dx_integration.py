"""Add Developer Experience Integration tables (Phase 6)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_dx_integration"
down_revision: str | None = "0009_release_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONBCompat = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("account_login", sa.String(length=120), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("repository_selection", sa.String(length=32), nullable=False),
        sa.Column("repositories", JSONBCompat, nullable=False),
        sa.Column("permissions", JSONBCompat, nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_gh_installations_ext_id_unique", "github_installations", ["installation_id"], unique=True)

    op.create_table(
        "github_webhook_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=True),
        sa.Column("installation_id", sa.Integer(), nullable=True),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("signature_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("payload_summary", JSONBCompat, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_gh_webhooks_delivery_unique", "github_webhook_events", ["delivery_id"], unique=True)
    op.create_index("ix_gh_webhooks_status_received", "github_webhook_events", ["processing_status", "received_at"])

    op.create_table(
        "github_check_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pull_request_id", sa.Uuid(as_uuid=True), sa.ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("external_check_run_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conclusion", sa.String(length=32), nullable=True),
        sa.Column("policy_mode", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details_url", sa.String(length=500), nullable=True),
        sa.Column("is_superseded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_gh_check_runs_pr_sha", "github_check_runs", ["pull_request_id", "commit_sha"])

    op.create_table(
        "pull_request_analysis_comments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pull_request_id", sa.Uuid(as_uuid=True), sa.ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("external_comment_id", sa.Integer(), nullable=True),
        sa.Column("marker_id", sa.String(length=128), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("config", JSONBCompat, nullable=False),
        sa.Column("publish_status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_pr_comments_pr_unique", "pull_request_analysis_comments", ["pull_request_id"], unique=True)

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("notification_id", sa.Uuid(as_uuid=True), sa.ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("channel_target", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notif_deliveries_provider_status", "notification_deliveries", ["provider", "status"])
    op.create_index("ix_notif_deliveries_dedupe", "notification_deliveries", ["dedupe_key"])

    op.create_table(
        "release_readiness_reports",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("release_id", sa.Uuid(as_uuid=True), sa.ForeignKey("releases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("readiness_outcome", sa.String(length=40), nullable=False),
        sa.Column("report_data", JSONBCompat, nullable=False),
        sa.Column("markdown_content", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_readiness_reports_release", "release_readiness_reports", ["release_id"])
    op.create_index("ix_readiness_reports_key_unique", "release_readiness_reports", ["report_key"], unique=True)

    op.create_table(
        "shareable_reports",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("report_id", sa.Uuid(as_uuid=True), sa.ForeignKey("release_readiness_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_shareable_reports_token_hash_unique", "shareable_reports", ["token_hash"], unique=True)
    op.create_index("ix_shareable_reports_report", "shareable_reports", ["report_id"])

    op.create_table(
        "share_link_access_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("share_id", sa.Uuid(as_uuid=True), sa.ForeignKey("shareable_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_share_access_logs_share", "share_link_access_logs", ["share_id", "accessed_at"])


def downgrade() -> None:
    op.drop_table("share_link_access_logs")
    op.drop_table("shareable_reports")
    op.drop_table("release_readiness_reports")
    op.drop_table("notification_deliveries")
    op.drop_table("pull_request_analysis_comments")
    op.drop_table("github_check_runs")
    op.drop_table("github_webhook_events")
    op.drop_table("github_installations")
