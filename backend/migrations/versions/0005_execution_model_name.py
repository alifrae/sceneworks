"""Persist the concrete model selected for each execution (WP8).

`model_profile` records provider-neutral intent; `model_name` records the
concrete model resolved when the execution was created. Existing executions
remain NULL because their historical model cannot be reconstructed safely.

The migration is deliberately adoption-safe. SceneWorks historically used
`create_all()` before Alembic, so a database with no migration history can
already contain a column from a newer declarative model. Such a database is
stamped at the baseline and then upgraded; blindly adding the column would fail
with "duplicate column name" instead of adopting the existing schema.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("executions", "model_name"):
        op.add_column(
            "executions",
            sa.Column("model_name", sa.String(length=300), nullable=True),
        )


def downgrade() -> None:
    if _has_column("executions", "model_name"):
        with op.batch_alter_table("executions") as batch:
            batch.drop_column("model_name")
