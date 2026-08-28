"""Persistent PCS runtime-control models (WP16)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models import utcnow

_EMPTY_JSON = text("'{}'")


class PcsProjectControl(Base):
    """Validated project-scoped PCS run profiles, runbooks and asset roots."""

    __tablename__ = "pcs_project_control"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), primary_key=True
    )
    config: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PcsRun(Base):
    """One SceneWorks-managed PCS process attached to an EngineeringSession."""

    __tablename__ = "pcs_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    engineering_session_id: Mapped[int] = mapped_column(
        ForeignKey("engineering_sessions.id"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True, index=True
    )
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("engineering_turns.id"), nullable=True, index=True
    )
    start_action_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    profile_name: Mapped[str] = mapped_column(String(120))
    process_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="STARTING", index=True)
    output_cursor: Mapped[int] = mapped_column(Integer, default=0)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=_EMPTY_JSON
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
