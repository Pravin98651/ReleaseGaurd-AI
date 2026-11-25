"""Add authentication & RBAC fields (`password_hash`, `github_login`, `token_version`) to users."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth_rbac_fields"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("github_login", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column("token_version", sa.Integer(), server_default="1", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("token_version")
        batch_op.drop_column("github_login")
        batch_op.drop_column("password_hash")
