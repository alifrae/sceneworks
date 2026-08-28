"""Add task binding, engineering turns and durable evidence.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "engineering_sessions" in tables and "task_id" not in _columns("engineering_sessions"):
        op.add_column(
            "engineering_sessions",
            sa.Column("task_id", sa.Integer(), nullable=True),
        )
        with op.batch_alter_table("engineering_sessions") as batch:
            batch.create_foreign_key(
                "fk_engineering_sessions_task_id_tasks", "tasks", ["task_id"], ["id"]
            )
            batch.create_index("ix_engineering_sessions_task_id", ["task_id"], unique=False)

    tables = _tables()
    if "engineering_turns" not in tables:
        op.create_table(
            "engineering_turns",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("engineering_session_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("intent", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["engineering_session_id"], ["engineering_sessions.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_engineering_turns_engineering_session_id", "engineering_turns", ["engineering_session_id"])
        op.create_index("ix_engineering_turns_task_id", "engineering_turns", ["task_id"])
        op.create_index("ix_engineering_turns_status", "engineering_turns", ["status"])

    tables = _tables()
    if "engineering_evidence" not in tables:
        op.create_table(
            "engineering_evidence",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("engineering_session_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("turn_id", sa.String(length=40), nullable=True),
            sa.Column("action_id", sa.String(length=40), nullable=False),
            sa.Column("category", sa.String(length=50), nullable=False),
            sa.Column("operation", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["engineering_session_id"], ["engineering_sessions.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.ForeignKeyConstraint(["turn_id"], ["engineering_turns.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("action_id", name="uq_engineering_evidence_action_id"),
        )
        for column in (
            "engineering_session_id",
            "task_id",
            "turn_id",
            "action_id",
            "category",
            "operation",
            "status",
        ):
            op.create_index(f"ix_engineering_evidence_{column}", "engineering_evidence", [column])


def downgrade() -> None:
    tables = _tables()
    if "engineering_evidence" in tables:
        op.drop_table("engineering_evidence")
    if "engineering_turns" in tables:
        op.drop_table("engineering_turns")
    if "engineering_sessions" in tables and "task_id" in _columns("engineering_sessions"):
        with op.batch_alter_table("engineering_sessions") as batch:
            batch.drop_index("ix_engineering_sessions_task_id")
            batch.drop_constraint("fk_engineering_sessions_task_id_tasks", type_="foreignkey")
            batch.drop_column("task_id")
