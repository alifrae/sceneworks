"""Persist project/task capabilities and advisory-role evidence (WP10).

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-23

The migration is adoption-safe for databases created historically with
``Base.metadata.create_all()`` before Alembic history existed. Each column is
added only when absent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("projects", "capability_profile"):
        op.add_column(
            "projects",
            sa.Column(
                "capability_profile",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    if not _has_column("tasks", "capability_requirements"):
        op.add_column(
            "tasks",
            sa.Column(
                "capability_requirements",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    if not _has_column("tasks", "advisory_results"):
        op.add_column(
            "tasks",
            sa.Column(
                "advisory_results",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade() -> None:
    for table, column in (
        ("tasks", "advisory_results"),
        ("tasks", "capability_requirements"),
        ("projects", "capability_profile"),
    ):
        if _has_column(table, column):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
