"""Persistent models owned by the WP14 direct engineering-control surface."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models import utcnow

_EMPTY_JSON = text("'{}'")


class EngineeringSession(Base):
    """Provider-neutral isolated worktree controlled through SceneWorks MCP."""

    __tablename__ = "engineering_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
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
