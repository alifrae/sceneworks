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
from app.models import all_models  # noqa: E402,F401  (registers tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Resolve the database URL, as a synchronous driver URL.

    Resolution order matters, and getting it wrong is dangerous: an earlier
    version consulted only ``get_settings()``, so a migration invoked
    programmatically against one database was executed against the *default*
    one instead. That attempted to create tables in a live database holding
    real project history.

    The caller's explicit choice therefore always wins over ambient settings.
    """
    # 1. `alembic -x db_url=...` — explicit operator override.
    if override := context.get_x_argument(as_dictionary=True).get("db_url"):
        return _strip_async_driver(override)

    # 2. Injected by app.db.migrations.ensure_schema_sync.
    if injected := config.attributes.get("db_url"):
        return _strip_async_driver(str(injected))

    # 3. Set on the config object (also set by the runner; belt and braces).
    if configured := config.get_main_option("sqlalchemy.url", None):
        return _strip_async_driver(configured)

    # 4. Ambient settings — only when nobody said otherwise.
    from app.config.settings import get_settings

    return _strip_async_driver(get_settings().database_url)


def _strip_async_driver(url: str) -> str:
    """`sqlite+aiosqlite:///x.db` -> `sqlite:///x.db`.

    Alembic runs migrations synchronously. The async driver only changes how
    Python talks to the file, not the file itself.
    """
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    """Emit SQL without a DBAPI connection (``alembic upgrade head --sql``)."""
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most columns in place; batch mode rewrites the
        # table instead. Required for anything beyond a plain ADD COLUMN.
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
