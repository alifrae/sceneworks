"""Persist project-wide engineering policy (WP4 compatibility port).

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-27

The original WP4 prototype used a separate ``project_policy`` table on an old
migration branch also numbered 0003. Current SceneWorks already owns revisions
0003..0006, so the compatible representation is a JSON policy value on Project:
it preserves the project-vs-task authority boundary without creating a second
Alembic head or a second one-to-one resource table.

The migration is adoption-safe for databases created historically with
``Base.metadata.create_all()`` before Alembic history existed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("projects", "engineering_policy"):
        op.add_column(
            "projects",
            sa.Column(
                "engineering_policy",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade() -> None:
    if _has_column("projects", "engineering_policy"):
        with op.batch_alter_table("projects") as batch:
            batch.drop_column("engineering_policy")
