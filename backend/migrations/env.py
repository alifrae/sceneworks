"""Alembic environment for SceneWorks.

The database URL always comes from SceneWorks settings, never from alembic.ini,
so a migration cannot be applied to a different database than the one the
application uses. An explicit override is still possible for tests and for
operating on a copy:

    alembic -x db_url=sqlite:///./data/backup.db upgrade head

SceneWorks runs on aiosqlite. Alembic's migration machinery is synchronous, so
the async driver is stripped for migration connections — the file is the same
database either way.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import Base  # noqa: E402
from app.models import all_models  # noqa: E402,F401  (registers core tables)
from app.engineering_models import (  # noqa: E402,F401
    EngineeringEvidence,
    EngineeringSession,
    EngineeringTurn,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Resolve the database URL, as a synchronous driver URL."""
    if override := context.get_x_argument(as_dictionary=True).get("db_url"):
        return _strip_async_driver(override)
    if injected := config.attributes.get("db_url"):
        return _strip_async_driver(str(injected))
    if configured := config.get_main_option("sqlalchemy.url", None):
        return _strip_async_driver(configured)
    from app.config.settings import get_settings

    return _strip_async_driver(get_settings().database_url)


def _strip_async_driver(url: str) -> str:
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _sync_url()

    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
