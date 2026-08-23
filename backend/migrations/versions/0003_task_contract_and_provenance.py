"""Add structured engineering contracts and persisted changed-file provenance.

WP4 needs a task-level contract that every role sees identically; WP6 needs the
files a completed implementation actually touched to be queryable without
re-running Git against a possibly cleaned-up worktree.

Both columns are additive JSON values with empty defaults so existing tasks keep
exactly their previous behaviour. The upgrade checks the existing table shape
because SceneWorks adopts pre-Alembic databases by stamping a baseline; test and
field databases may legitimately contain an additive column already.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("tasks")}
    if "engineering_contract" not in existing:
        op.add_column(
            "tasks",
            sa.Column(
                "engineering_contract",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    if "changed_files" not in existing:
        op.add_column(
            "tasks",
            sa.Column(
                "changed_files",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("tasks")}
    with op.batch_alter_table("tasks") as batch:
        if "changed_files" in existing:
            batch.drop_column("changed_files")
        if "engineering_contract" in existing:
            batch.drop_column("engineering_contract")
