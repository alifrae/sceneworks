"""Baseline schema (the V1-V3.0 schema previously created by create_all)

This revision reproduces exactly what `Base.metadata.create_all` produced, so
that a database created before migrations existed can be **stamped** at this
revision rather than rebuilt. Stamping is what makes adoption non-destructive:
the real deployment at the time of writing held 145 projects, 112 tasks, 270
executions and 4340 events, none of which may be lost to gain a version table.

Do not edit this revision to change the schema. Add a new one.

Revision ID: 0001
Revises:
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("repository_path", sa.String(length=1000), nullable=False),
        sa.Column("default_branch", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("architecture_context_paths", sa.JSON(), nullable=False),
        sa.Column("test_commands", sa.JSON(), nullable=False),
        sa.Column("build_commands", sa.JSON(), nullable=False),
        sa.Column("worktree_root_override", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("current_role", sa.String(length=50), nullable=True),
        sa.Column("current_execution_id", sa.String(length=40), nullable=True),
        sa.Column("base_commit", sa.String(length=100), nullable=True),
        sa.Column("task_branch", sa.String(length=200), nullable=True),
        sa.Column("worktree_path", sa.String(length=1000), nullable=True),
        sa.Column("architecture_worktree_path", sa.String(length=1000), nullable=True),
        sa.Column("review_worktree_path", sa.String(length=1000), nullable=True),
        sa.Column("result_commit", sa.String(length=100), nullable=True),
        sa.Column("architecture_result", sa.Text(), nullable=True),
        sa.Column("implementation_summary", sa.Text(), nullable=True),
        sa.Column("review_result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "executions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("backend", sa.String(length=100), nullable=False),
        sa.Column("model_profile", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("workspace", sa.JSON(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt", sa.Text(), nullable=True),
        sa.Column("prompt_preview", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "role", "started_at", name="uq_task_role_started"),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("execution_id", sa.String(length=40), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_execution_id"), "events", ["execution_id"])
    op.create_index(op.f("ix_events_task_id"), "events", ["task_id"])
    op.create_index(op.f("ix_events_timestamp"), "events", ["timestamp"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_execution_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "project_memory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("source_task_id", sa.Integer(), nullable=True),
        sa.Column("source_execution_id", sa.String(length=40), nullable=True),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["project_memory.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_memory_project_id"), "project_memory", ["project_id"])


def downgrade() -> None:
    """Drop everything. Only meaningful on a database created by this revision.

    Never run against a database that was stamped rather than built by 0001:
    that database predates migrations and its data is the whole point.
    """
    op.drop_index(op.f("ix_project_memory_project_id"), table_name="project_memory")
    op.drop_table("project_memory")
    op.drop_table("app_settings")
    op.drop_table("artifacts")
    op.drop_index(op.f("ix_events_timestamp"), table_name="events")
    op.drop_index(op.f("ix_events_task_id"), table_name="events")
    op.drop_index(op.f("ix_events_execution_id"), table_name="events")
    op.drop_table("events")
    op.drop_table("executions")
    op.drop_table("tasks")
    op.drop_table("projects")
