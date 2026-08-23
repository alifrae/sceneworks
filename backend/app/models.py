"""Persistent domain models (SQLAlchemy 2.x declarative).

Schema notes:
- Initiative and WorkPackage provide durable objective decomposition above Task.
- Task holds workflow-scoped, coarse results; fine-grained traceability lives in
  Execution and Event rows.
- Execution is the unit of agent invocation (one row per agent run).
- Event rows are the durable record of everything an execution produced.
- Artifact stores company-role outputs that are not attached to a task.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_EMPTY_JSON = text("'{}'")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    repository_path: Mapped[str] = mapped_column(String(1000))
    default_branch: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(50), default="active")
    architecture_context_paths: Mapped[list] = mapped_column(JSON, default=list)
    test_commands: Mapped[list] = mapped_column(JSON, default=list)
    build_commands: Mapped[list] = mapped_column(JSON, default=list)
    # Provider/model-neutral professional capability overlays. These describe
    # relevant skills/domains/methods, never project facts. A server default is
    # deliberate: older/raw insert paths must remain compatible with a
    # create_all-produced database, matching migration 0006 behavior.
    capability_profile: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    worktree_root_override: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    tasks: Mapped[list[Task]] = relationship(back_populates="project")
    initiatives: Mapped[list[Initiative]] = relationship(back_populates="project")


class Initiative(Base):
    """A durable project objective decomposed into ordered work packages."""

    __tablename__ = "initiatives"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    objective: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="planned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="initiatives")
    work_packages: Mapped[list[WorkPackage]] = relationship(back_populates="initiative", order_by="WorkPackage.sequence")


class WorkPackage(Base):
    """One bounded, dependency-aware unit of an Initiative."""

    __tablename__ = "work_packages"
    __table_args__ = (UniqueConstraint("initiative_id", "key", name="uq_work_package_initiative_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="planned")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    initiative: Mapped[Initiative] = relationship(back_populates="work_packages")
    tasks: Mapped[list[Task]] = relationship(back_populates="work_package")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    work_package_id: Mapped[int | None] = mapped_column(ForeignKey("work_packages.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="NEW")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    current_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_execution_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_commit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    architecture_worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    review_worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    result_commit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    architecture_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    engineering_contract: Mapped[dict] = mapped_column(JSON, default=dict)
    # More-specific overlays layered on top of the project's capability profile.
    capability_requirements: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    # Advisory role outputs survive the Architect phase independently instead of
    # being flattened into/replaced by architecture_result.
    advisory_results: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="tasks")
    work_package: Mapped[WorkPackage | None] = relationship(back_populates="tasks")
    executions: Mapped[list[Execution]] = relationship(back_populates="task")


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("task_id", "role", "started_at", name="uq_task_role_started"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(50))
    backend: Mapped[str] = mapped_column(String(100))
    model_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Concrete model selected from the profile when this execution was created.
    # Persisted so queued/restarted work cannot drift with later setting changes.
    model_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
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
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    execution: Mapped[Execution | None] = relationship(back_populates="events")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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
    source_commit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("project_memory.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


all_models = (Project, Initiative, WorkPackage, Task, Execution, Event, Artifact, AppSetting, ProjectMemory)
