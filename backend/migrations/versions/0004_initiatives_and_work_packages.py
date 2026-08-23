"""Add Project -> Initiative -> WorkPackage -> Task hierarchy (WP5).

The pre-WP5 product only had Project -> Task. Initiatives and work packages
provide durable decomposition, dependency and acceptance-criteria records above
individual execution tasks without changing the existing task workflow.

The migration is idempotent with the repository's legacy-adoption test fixture:
that fixture is created from current ORM metadata, so additive structures may
already exist even though no Alembic history is present.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "initiatives" not in tables:
        op.create_table(
            "initiatives",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="planned"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_initiatives_project_id", "initiatives", ["project_id"])

    if "work_packages" not in tables:
        op.create_table(
            "work_packages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("initiative_id", sa.Integer(), sa.ForeignKey("initiatives.id"), nullable=False),
            sa.Column("key", sa.String(length=100), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="planned"),
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("depends_on", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("acceptance_criteria", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("initiative_id", "key", name="uq_work_package_initiative_key"),
        )
        op.create_index("ix_work_packages_initiative_id", "work_packages", ["initiative_id"])

    task_columns = {column["name"] for column in sa.inspect(bind).get_columns("tasks")}
    if "work_package_id" not in task_columns:
        # SQLite cannot ALTER TABLE ADD CONSTRAINT directly. Batch mode rebuilds
        # the table while preserving task rows and also works on other backends.
        with op.batch_alter_table("tasks") as batch:
            batch.add_column(sa.Column("work_package_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_tasks_work_package_id",
                "work_packages",
                ["work_package_id"],
                ["id"],
            )
            batch.create_index("ix_tasks_work_package_id", ["work_package_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "tasks" in tables:
        task_columns = {column["name"] for column in sa.inspect(bind).get_columns("tasks")}
        if "work_package_id" in task_columns:
            with op.batch_alter_table("tasks") as batch:
                batch.drop_index("ix_tasks_work_package_id")
                batch.drop_constraint("fk_tasks_work_package_id", type_="foreignkey")
                batch.drop_column("work_package_id")
    if "work_packages" in tables:
        op.drop_index("ix_work_packages_initiative_id", table_name="work_packages")
        op.drop_table("work_packages")
    if "initiatives" in tables:
        op.drop_index("ix_initiatives_project_id", table_name="initiatives")
        op.drop_table("initiatives")
