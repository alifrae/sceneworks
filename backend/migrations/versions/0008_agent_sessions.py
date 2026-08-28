"""Persist ChatGPT-supervised advanced agent sessions (WP11).

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-27

Advanced sessions are intentionally separate from Task/Execution workflow state:
they let an external MCP reasoning client iteratively supervise Gemini CLI while
SceneWorks retains workspace isolation, provenance and explicit permissions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("agent_sessions"):
        return
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("backend", sa.String(length=100), nullable=False, server_default="gemini_acp"),
        sa.Column("provider_session_id", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="STARTING"),
        sa.Column("base_commit", sa.String(length=100), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("worktree_path", sa.String(length=1000), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("model_name", sa.String(length=300), nullable=True),
        sa.Column("provider_capabilities", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_result", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_sessions_project_id", "agent_sessions", ["project_id"])
    op.create_index("ix_agent_sessions_provider_session_id", "agent_sessions", ["provider_session_id"])
    op.create_index("ix_agent_sessions_status", "agent_sessions", ["status"])


def downgrade() -> None:
    if _has_table("agent_sessions"):
        op.drop_table("agent_sessions")
