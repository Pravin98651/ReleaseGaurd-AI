"""Add notifications and notification_preferences tables.

Revision ID: 0004_notifications
Revises: 0003_scheduling
Create Date: 2026-11-25 20:32:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONBCompat, TZDateTime
from app.models.enums import NotificationCategory, str_enum

revision: str = "0004_notifications"
down_revision: str | Sequence[str] | None = "0003_scheduling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("category", str_enum(NotificationCategory, length=60), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("environment_name", sa.String(length=64), nullable=True),
        sa.Column("action_href", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", JSONBCompat, nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("read_at", TZDateTime(), nullable=True),
        sa.Column("is_resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", TZDateTime(), nullable=True),
        sa.Column(
            "created_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notifications_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index(
        "ix_notifications_user_read_created",
        "notifications",
        ["user_id", "is_read", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_category_created",
        "notifications",
        ["category", "created_at"],
        unique=False,
    )

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False),
        sa.Column("severity_threshold", sa.String(length=20), nullable=False),
        sa.Column("muted_categories", JSONBCompat, nullable=False),
        sa.Column(
            "updated_at",
            TZDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notification_preferences_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_preferences"),
        sa.UniqueConstraint("user_id", name="uq_notification_preferences_user_id"),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_index("ix_notifications_category_created", table_name="notifications")
    op.drop_index("ix_notifications_user_read_created", table_name="notifications")
    op.drop_table("notifications")
