"""Add PCS runtime configuration and managed-run provenance.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "pcs_project_control" not in tables:
        op.create_table(
            "pcs_project_control",
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("project_id"),
        )

    if "pcs_runs" not in tables:
        op.create_table(
            "pcs_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("engineering_session_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("turn_id", sa.String(length=40), nullable=True),
            sa.Column("start_action_id", sa.String(length=40), nullable=True),
            sa.Column("profile_name", sa.String(length=120), nullable=False),
            sa.Column("process_id", sa.String(length=64), nullable=False),
            sa.Column("pid", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="STARTING"),
            sa.Column("output_cursor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["engineering_session_id"], ["engineering_sessions.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.ForeignKeyConstraint(["turn_id"], ["engineering_turns.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("process_id"),
        )
        for column in (
            "project_id",
            "engineering_session_id",
            "task_id",
            "turn_id",
            "process_id",
            "status",
        ):
            op.create_index(
                op.f(f"ix_pcs_runs_{column}"),
                "pcs_runs",
                [column],
                unique=False,
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "pcs_runs" in tables:
        indexes = {row["name"] for row in inspector.get_indexes("pcs_runs")}
        for column in (
            "project_id",
            "engineering_session_id",
            "task_id",
            "turn_id",
            "process_id",
            "status",
        ):
            name = op.f(f"ix_pcs_runs_{column}")
            if name in indexes:
                op.drop_index(name, table_name="pcs_runs")
        op.drop_table("pcs_runs")
    if "pcs_project_control" in tables:
        op.drop_table("pcs_project_control")
