"""Persistent domain models (SQLAlchemy 2.x declarative).

Schema notes:
- Initiative and WorkPackage provide durable objective decomposition above Task.
- Task holds workflow-scoped, coarse results; fine-grained traceability lives in
  Execution and Event rows.
- TaskAttachment stores immutable SceneWorks-owned context outside repositories.
- Execution is the unit of governed role invocation (one row per agent run).
- AgentSession persists ChatGPT-supervised advanced Gemini sessions independently
  from governed Task workflows.
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
_TASK_TYPE_DEFAULT = text("'task'")
_TASK_MODE_DEFAULT = text("'auto'")


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
    engineering_policy: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
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
    # WP13 separates backlog classification from execution intent. Existing
    # rows migrate to task/auto; resolved_mode is populated when work starts.
    work_item_type: Mapped[str] = mapped_column(
        String(20), default="task", server_default=_TASK_TYPE_DEFAULT
    )
    requested_mode: Mapped[str] = mapped_column(
        String(20), default="auto", server_default=_TASK_MODE_DEFAULT
    )
    resolved_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
    capability_requirements: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    advisory_results: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="tasks")
    work_package: Mapped[WorkPackage | None] = relationship(back_populates="tasks")
    executions: Mapped[list[Execution]] = relationship(back_populates="task")
    attachments: Mapped[list[TaskAttachment]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskAttachment.created_at"
    )


class TaskAttachment(Base):
    """Immutable user-provided context associated with a task.

    ``storage_key`` is relative to the configured attachment root. Attachment
    bytes never live in the managed repository or an agent worktree.
    """

    __tablename__ = "task_attachments"
    __table_args__ = (
        UniqueConstraint("task_id", "sha256", name="uq_task_attachment_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(50), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="attachments")


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("task_id", "role", "started_at", name="uq_task_role_started"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(50))
    backend: Mapped[str] = mapped_column(String(100))
    model_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
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


class AgentSession(Base):
    """Persistent external-supervisor session over a provider agent."""

    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    backend: Mapped[str] = mapped_column(String(100), default="gemini_acp")
    provider_session_id: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="STARTING", index=True)
    base_commit: Mapped[str] = mapped_column(String(100))
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    model_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    provider_capabilities: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    last_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


all_models = (
    Project,
    Initiative,
    WorkPackage,
    Task,
    TaskAttachment,
    Execution,
    AgentSession,
    Event,
    Artifact,
    AppSetting,
    ProjectMemory,
)
