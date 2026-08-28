"""Add lightweight work-management and execution-intent fields to tasks.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns("tasks")
    if not columns:
        return
    if "work_item_type" not in columns:
        op.add_column(
            "tasks",
            sa.Column("work_item_type", sa.String(length=20), nullable=False, server_default="task"),
        )
    if "requested_mode" not in columns:
        op.add_column(
            "tasks",
            sa.Column("requested_mode", sa.String(length=20), nullable=False, server_default="auto"),
        )
    if "resolved_mode" not in columns:
        op.add_column(
            "tasks",
            sa.Column("resolved_mode", sa.String(length=20), nullable=True),
        )


def downgrade() -> None:
    columns = _columns("tasks")
    for column in ("resolved_mode", "requested_mode", "work_item_type"):
        if column in columns:
            op.drop_column("tasks", column)
