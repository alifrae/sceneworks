"""Persistent domain models (SQLAlchemy 2.x declarative).

Schema notes:
- Task holds workflow-scoped, coarse results as text blobs; fine-grained
  traceability lives in Execution and Event rows.
- Execution is the unit of agent invocation (one row per agent run).
- Event rows are the durable record of everything an execution produced.
- Artifact stores company-role outputs (decisions, analyses) that are not
  attached to a task.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    repository_path: Mapped[str] = mapped_column(String(1000))
    default_branch: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(50), default="active")
    # JSON list of repo-relative paths (e.g. ["docs/architecture.md"]).
    architecture_context_paths: Mapped[list] = mapped_column(JSON, default=list)
    test_commands: Mapped[list] = mapped_column(JSON, default=list)
    build_commands: Mapped[list] = mapped_column(JSON, default=list)
    worktree_root_override: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    tasks: Mapped[list[Task]] = relationship(back_populates="project")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="NEW")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    current_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_execution_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_commit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    architecture_worktree_path: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )
    review_worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    result_commit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    architecture_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # WP4: explicit task obligations shared by Architect, Engineer and Reviewer.
    # Kept as a JSON object so adding contract dimensions does not force a schema
    # migration; validation and normalisation live in the Pydantic API schema.
    engineering_contract: Mapped[dict] = mapped_column(JSON, default=dict)
    # WP6: authoritative repo-relative paths observed from Git at implementation
    # completion. Never populated from an agent-written summary.
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    project: Mapped[Project] = relationship(back_populates="tasks")
    executions: Mapped[list[Execution]] = relationship(back_populates="task")


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("task_id", "role", "started_at", name="uq_task_role_started"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(50))
    backend: Mapped[str] = mapped_column(String(100))
    model_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    workspace: Mapped[dict] = mapped_column(JSON, default=dict)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task | None] = relationship(back_populates="executions")
    events: Mapped[list[Event]] = relationship(back_populates="execution")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("executions.id"), nullable=True, index=True
    )
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    execution: Mapped[Execution | None] = relationship(back_populates="events")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))  # e.g. "company_decision"
    role: Mapped[str] = mapped_column(String(50))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    source_execution_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProjectMemory(Base):
    __tablename__ = "project_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    source_execution_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Repository state the decision was made against. NULL means "not
    #: recorded" — never "the base commit". Memories created before this column
    #: existed (migration 0002) legitimately have no commit to attribute.
    source_commit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("project_memory.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


all_models = (Project, Task, Execution, Event, Artifact, AppSetting, ProjectMemory)
