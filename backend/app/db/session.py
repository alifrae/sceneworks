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
        # Enable WAL for SQLite so reads do not block the writer.
        event.listen(
            __import__("sqlalchemy").engine.Engine, "connect", _set_sqlite_pragma
        )

    engine = create_async_engine(url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def init_db(engine: AsyncEngine) -> None:
    """Create tables if they do not exist (V1 uses create_all; no Alembic yet)."""
    from app.models import all_models  # noqa: F401  (ensure tables are registered)

    # SQLite does not create the directory; ensure it exists.
    if str(engine.url).startswith("sqlite") and engine.url.database not in (None, ":memory:"):
        Path(engine.url.database).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db(engine: AsyncEngine) -> None:
    await engine.dispose()
