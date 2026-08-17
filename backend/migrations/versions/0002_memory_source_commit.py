"""Record the commit a project memory's decision was made against

Closes the gap WP2 deferred here explicitly: a memory could record its source
task, execution and authoring role, but not the repository state the decision was
about. Without it, "all point cloud IO goes through api/facade.py" cannot be
attributed to the snapshot in which that was true.

Nullable, because every existing memory predates the column and no commit can be
invented for it. `NULL` means "not recorded", never "the base commit".

This is also the first real exercise of the upgrade path: it adds a column to a
table holding live data.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # A plain ADD COLUMN of a nullable column is the one ALTER SQLite supports
    # directly, so no table rewrite is needed and existing rows are untouched.
    op.add_column(
        "project_memory",
        sa.Column("source_commit", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    # SQLite before 3.35 cannot DROP COLUMN; batch mode rewrites the table.
    with op.batch_alter_table("project_memory") as batch:
        batch.drop_column("source_commit")
