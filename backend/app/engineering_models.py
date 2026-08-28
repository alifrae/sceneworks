"""Persistent models for provider-neutral engineering control and evidence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models import utcnow

_EMPTY_JSON = text("'{}'")


class EngineeringSession(Base):
    """Provider-neutral isolated worktree controlled through SceneWorks MCP."""

    __tablename__ = "engineering_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # Optional governed-task binding. The session remains usable without a task,
    # but when present every turn/evidence row snapshots this task id so the
    # verification trail can answer whether that exact task was actually closed.
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    runtime: Mapped[str] = mapped_column(String(100), default="native")
    status: Mapped[str] = mapped_column(String(30), default="STARTING", index=True)
    base_commit: Mapped[str] = mapped_column(String(100))
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    default_backend: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(300), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EngineeringTurn(Base):
    """One explicit supervisor iteration inside an EngineeringSession."""

    __tablename__ = "engineering_turns"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    engineering_session_id: Mapped[int] = mapped_column(
        ForeignKey("engineering_sessions.id"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    intent: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EngineeringEvidence(Base):
    """Durable SceneWorks-captured evidence for one engineering action."""

    __tablename__ = "engineering_evidence"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_engineering_evidence_action_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    engineering_session_id: Mapped[int] = mapped_column(
        ForeignKey("engineering_sessions.id"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("engineering_turns.id"), nullable=True, index=True
    )
    action_id: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    operation: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, server_default=_EMPTY_JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
