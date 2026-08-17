"""Schema migration runner.

Replaces `Base.metadata.create_all`, which created missing *tables* and silently
did nothing about anything else. Adding a column to an existing database was a
no-op, so the application then failed at query time with no indication that the
schema was stale (docs/wp0-baseline-audit.md, F7).

Three situations, each handled explicitly:

1. **Fresh database** — no tables. Run every migration from the beginning.
2. **Legacy database** — SceneWorks tables exist but there is no
   ``alembic_version``. It was created by ``create_all`` before migrations
   existed. It is **stamped** at the baseline revision and then upgraded.
   It is never rebuilt: the deployment this was written against held 145
   projects, 112 tasks and 4340 events.
3. **Managed database** — ``alembic_version`` present. Upgrade to head.

A migration failure aborts startup with the actual error. The alternative —
continuing on a half-migrated schema — turns one clear failure into arbitrarily
many confusing ones.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.config.settings import Settings

logger = logging.getLogger("sceneworks.db.migrations")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"

#: The revision a pre-migrations database is stamped at. It reproduces exactly
#: what create_all produced, so stamping is safe.
BASELINE_REVISION = "0001"

#: Tables that prove a database is a SceneWorks database rather than an empty
#: file. `projects` is the root of the schema and has existed since V1.
LEGACY_MARKER_TABLES = ("projects", "tasks", "executions")


class MigrationError(RuntimeError):
    """Raised when the schema cannot be brought to head."""


@dataclass
class SchemaState:
    """What was found, and what was done about it. Reported, not inferred."""

    fresh: bool
    stamped_baseline: bool
    revision_before: str | None
    revision_after: str | None

    @property
    def action(self) -> str:
        if self.fresh:
            return "created a new database at head"
        if self.stamped_baseline:
            return (
                f"adopted a pre-migrations database: stamped {BASELINE_REVISION} "
                f"and upgraded to {self.revision_after}"
            )
        if self.revision_before == self.revision_after:
            return f"already at head ({self.revision_after})"
        return f"upgraded {self.revision_before} -> {self.revision_after}"


def _sync_url(database_url: str) -> str:
    return database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _alembic_config(database_url: str):
    from alembic.config import Config

    if not ALEMBIC_INI.is_file():  # pragma: no cover - packaging guard
        raise MigrationError(f"alembic.ini not found at {ALEMBIC_INI}")
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", _sync_url(database_url))
    # env.py reads the URL from settings by default; -x wins, and using it here
    # keeps the runner and a manual `alembic -x db_url=...` on the same path.
    config.cmd_opts = None
    config.attributes["db_url"] = _sync_url(database_url)
    return config


def _ensure_parent_directory(database_url: str) -> None:
    url = _sync_url(database_url)
    if not url.startswith("sqlite"):
        return
    path = url.split("///", 1)[-1]
    if path in ("", ":memory:") or path.startswith(":memory:"):
        return
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def ensure_schema_sync(settings: Settings) -> SchemaState:
    """Bring the database to head. Synchronous; safe to call in a thread."""
    from alembic import command
    from alembic.runtime.migration import MigrationContext

    database_url = settings.database_url
    _ensure_parent_directory(database_url)

    # Engine construction is inside the guard too: a missing driver
    # ("No module named 'psycopg'") is as much a migration failure as a bad
    # revision, and its message must be redacted like any other.
    engine = None
    try:
        engine = create_engine(_sync_url(database_url), future=True)
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            has_version_table = "alembic_version" in tables
            has_sceneworks_tables = any(t in tables for t in LEGACY_MARKER_TABLES)
            current = (
                MigrationContext.configure(connection).get_current_revision()
                if has_version_table
                else None
            )

        fresh = not has_sceneworks_tables and not has_version_table
        stamped = False

        config = _alembic_config(database_url)

        if has_sceneworks_tables and not has_version_table:
            # Adoption. Stamp, never recreate.
            logger.warning(
                "database has SceneWorks tables but no migration history; "
                "stamping baseline %s and upgrading (no data is modified)",
                BASELINE_REVISION,
            )
            command.stamp(config, BASELINE_REVISION)
            stamped = True
            current = BASELINE_REVISION

        command.upgrade(config, "head")

        with engine.connect() as connection:
            after = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:  # noqa: BLE001 - surfaced with context
        raise MigrationError(
            f"database schema migration failed for {_redact(database_url)}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        if engine is not None:
            engine.dispose()

    state = SchemaState(
        fresh=fresh,
        stamped_baseline=stamped,
        revision_before=current,
        revision_after=after,
    )
    logger.info("schema: %s", state.action)
    return state


async def ensure_schema(settings: Settings) -> SchemaState:
    """Async wrapper. Alembic is synchronous, so it runs in a worker thread."""
    return await asyncio.to_thread(ensure_schema_sync, settings)


def current_revision(settings: Settings) -> str | None:
    """The revision a database is on, or None if it has no history."""
    from alembic.runtime.migration import MigrationContext

    engine = create_engine(_sync_url(settings.database_url), future=True)
    try:
        with engine.connect() as connection:
            if "alembic_version" not in set(inspect(connection).get_table_names()):
                return None
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def head_revision() -> str:
    """The newest revision the code knows about."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(MIGRATIONS_DIR))
    head = script.get_current_head()
    if head is None:  # pragma: no cover - only with an empty versions dir
        raise MigrationError("no migrations found")
    return head


def is_up_to_date(settings: Settings) -> bool:
    return current_revision(settings) == head_revision()


def _redact(database_url: str) -> str:
    """Never let a password reach a log line."""
    if "@" not in database_url:
        return database_url
    scheme, _, rest = database_url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"


def verify_schema_sync(settings: Settings) -> None:
    """Fail loudly if the schema is not at head.

    Used by the release-validation job: a build must not ship with migrations
    that were never applied to the shipped database template.
    """
    current = current_revision(settings)
    head = head_revision()
    if current != head:
        raise MigrationError(
            f"database is at revision {current!r} but the code expects {head!r}. "
            "Run `alembic upgrade head`."
        )
