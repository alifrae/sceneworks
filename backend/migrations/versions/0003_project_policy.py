"""Add project_policy: the WP4 engineering-contract table

A project's engineering invariants -- protected paths, required review checks,
architecture invariants, forbidden dependency directions, documentation and
performance requirements, go/no-go commands, release requirements -- as
structured, individually enforceable rules distinct from the free-text
architecture context files a project already had
(`Project.architecture_context_paths`). See app/models.py `ProjectPolicy` and
docs/project-policy.md for the full contract.

New table, so there is nothing to backfill: no existing project has a policy
row until one is created via the API.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "project_policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("protected_paths", sa.JSON(), nullable=False),
        sa.Column("go_no_go_commands", sa.JSON(), nullable=False),
        sa.Column("forbidden_dependency_directions", sa.JSON(), nullable=False),
        sa.Column("architecture_invariants", sa.JSON(), nullable=False),
        sa.Column("documentation_requirements", sa.JSON(), nullable=False),
        sa.Column("performance_constraints", sa.JSON(), nullable=False),
        sa.Column("required_review_checks", sa.JSON(), nullable=False),
        sa.Column("release_requirements", sa.JSON(), nullable=False),
        sa.Column("policy_file_paths", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # A single unique index, not a separate UniqueConstraint plus a non-unique
    # index: `mapped_column(unique=True, index=True)` on the ORM side compiles
    # to exactly one unique index (verified against
    # CreateTable(ProjectPolicy.__table__) before writing this), so this must
    # match it precisely rather than merely achieve the same intent.
    op.create_index(
        op.f("ix_project_policy_project_id"),
        "project_policy",
        ["project_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_policy_project_id"), table_name="project_policy")
    op.drop_table("project_policy")
