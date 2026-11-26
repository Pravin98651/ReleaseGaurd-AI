"""Migrate retired gemini-2.5-flash workspace_settings default to gemini-3.5-flash-lite."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_gemini_35_default"
down_revision: str | None = "0014_flag_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE workspace_settings "
            "SET gemini_model = 'gemini-3.5-flash-lite' "
            "WHERE gemini_model IN ('gemini-2.5-flash', 'gemini-2.5-flash-lite')"
        )
    )


def downgrade() -> None:
    # Data migration only; do not revert live workspace settings to retired models.
    pass
