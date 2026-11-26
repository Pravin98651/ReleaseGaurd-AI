"""Widen external GitHub ID columns from 32-bit Integer to 64-bit BigInteger."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_bigint_github_ids"
down_revision: str | None = "0012_productivity_benchmarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("github_installations", "installation_id", False),
    ("github_webhook_events", "installation_id", True),
    ("github_check_runs", "external_check_run_id", True),
    ("pull_request_analysis_comments", "external_comment_id", True),
    ("organizations", "github_org_id", True),
    ("organization_repositories", "github_repository_id", True),
)


def upgrade() -> None:
    for table_name, col_name, nullable in _COLUMNS:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                col_name,
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=nullable,
                nullable=nullable,
            )


def downgrade() -> None:
    for table_name, col_name, nullable in reversed(_COLUMNS):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                col_name,
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=nullable,
                nullable=nullable,
            )
