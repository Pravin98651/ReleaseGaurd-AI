"""Add lifecycle_status and lifecycle_details columns to feature_flags."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_flag_lifecycle"
down_revision: str | None = "0013_bigint_github_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONBCompat = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    json_default = sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'")

    op.add_column(
        "feature_flags",
        sa.Column(
            "lifecycle_status",
            sa.String(length=50),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "feature_flags",
        sa.Column(
            "lifecycle_details",
            JSONBCompat,
            nullable=False,
            server_default=json_default,
        ),
    )
    op.create_index("ix_feature_flags_lifecycle_status", "feature_flags", ["lifecycle_status"])


def downgrade() -> None:
    op.drop_index("ix_feature_flags_lifecycle_status", table_name="feature_flags")
    op.drop_column("feature_flags", "lifecycle_details")
    op.drop_column("feature_flags", "lifecycle_status")
