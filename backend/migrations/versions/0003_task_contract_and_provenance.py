"""Add structured engineering contracts and persisted changed-file provenance.

WP4 needs a task-level contract that every role sees identically; WP6 needs the
files a completed implementation actually touched to be queryable without
re-running Git against a possibly cleaned-up worktree.

Both columns are additive JSON values with empty defaults so existing tasks keep
exactly their previous behaviour.

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
    op.add_column(
        "tasks",
        sa.Column(
            "engineering_contract",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
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
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("changed_files")
        batch.drop_column("engineering_contract")
