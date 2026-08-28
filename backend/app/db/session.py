"""Database engine and session management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.settings import Settings


class Base(DeclarativeBase):
    pass


def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # pragma: no cover - trivial
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def create_engine_and_sessionmaker(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    url = settings.database_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"timeout": 30}
        event.listen(
            __import__("sqlalchemy").engine.Engine, "connect", _set_sqlite_pragma
        )

    engine = create_async_engine(url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def init_db(engine: AsyncEngine, settings: Settings | None = None) -> None:
    """Bring the schema to Alembic head and register all application metadata."""
    from app.models import all_models  # noqa: F401
    from app.engineering_models import (  # noqa: F401
        EngineeringEvidence,
        EngineeringSession,
        EngineeringTurn,
    )
    from app.pcs_models import PcsProjectControl, PcsRun  # noqa: F401
    from app.db.migrations import ensure_schema

    if str(engine.url).startswith("sqlite") and engine.url.database not in (None, ":memory:"):
        Path(engine.url.database).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )

    if settings is None:
        settings = Settings(database_url=engine.url.render_as_string(hide_password=False))

    await ensure_schema(settings)


async def close_db(engine: AsyncEngine) -> None:
    await engine.dispose()
