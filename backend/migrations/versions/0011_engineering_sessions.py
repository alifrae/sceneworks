"""Add provider-neutral engineering sessions for direct MCP control.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "engineering_sessions" in inspector.get_table_names():
        return
    op.create_table(
        "engineering_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("runtime", sa.String(length=100), nullable=False, server_default="native"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="STARTING"),
        sa.Column("base_commit", sa.String(length=100), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("worktree_path", sa.String(length=1000), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("default_backend", sa.String(length=100), nullable=True),
        sa.Column("default_model", sa.String(length=300), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_engineering_sessions_project_id"),
        "engineering_sessions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engineering_sessions_status"),
        "engineering_sessions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "engineering_sessions" not in inspector.get_table_names():
        return
    op.drop_index(op.f("ix_engineering_sessions_status"), table_name="engineering_sessions")
    op.drop_index(op.f("ix_engineering_sessions_project_id"), table_name="engineering_sessions")
    op.drop_table("engineering_sessions")
