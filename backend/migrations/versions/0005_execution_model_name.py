"""Persist the concrete model selected for each execution (WP8).

`model_profile` records provider-neutral intent; `model_name` records the
concrete model resolved when the execution was created. Existing executions
remain NULL because their historical model cannot be reconstructed safely.

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


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("model_name", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("executions") as batch:
        batch.drop_column("model_name")
