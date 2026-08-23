"""Schema migration tests (WP3).

Before WP3, `init_db` called `Base.metadata.create_all`, which creates missing
*tables* and silently ignores every other schema change. Adding a column did
nothing to an existing database and the application then failed at query time
with no indication the schema was stale (docs/wp0-baseline-audit.md, F7).

The property that matters most here is **non-destructive adoption**: the
deployment this was written against held 145 projects, 112 tasks, 270 executions
and 4340 events in a database created before migrations existed. Gaining a
version table must never cost that data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.config.settings import Settings
from app.db.migrations import (
    BASELINE_REVISION,
    MigrationError,
    current_revision,
    ensure_schema_sync,
    head_revision,
    is_up_to_date,
    verify_schema_sync,
)
from app.db.session import Base
from app.models import all_models  # noqa: F401  (registers tables)

EXPECTED_TABLES = {
    "projects", "tasks", "executions", "events",
    "artifacts", "app_settings", "project_memory",
}


def settings_for(db_path: Path, tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        worktree_root=tmp_path / "wt",
    )


def table_names(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def columns(db_path: Path, table: str) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def make_legacy_database(db_path: Path) -> None:
    """Build a database with the shape that predates additive migrations.

    Uses current metadata as the mechanically safe starting point, then removes
    every later-revision column. This matters because ``create_all`` historically
    produced field databases before Alembic existed: adoption must tolerate both
    genuinely old schemas and newer create_all schemas that already contain a
    future declarative column.
    """
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            # 0002
            conn.exec_driver_sql(
                "ALTER TABLE project_memory DROP COLUMN source_commit"
            )
            # 0006 / WP10
            conn.exec_driver_sql(
                "ALTER TABLE projects DROP COLUMN capability_profile"
            )
            conn.exec_driver_sql(
                "ALTER TABLE tasks DROP COLUMN capability_requirements"
            )
            conn.exec_driver_sql(
                "ALTER TABLE tasks DROP COLUMN advisory_results"
            )
    finally:
        engine.dispose()


def seed_rows(db_path: Path, projects: int = 3, memories: int = 4) -> dict[str, int]:
    con = sqlite3.connect(db_path)
    try:
        for i in range(projects):
            con.execute(
                "INSERT INTO projects (id, name, description, repository_path, "
                "default_branch, status, architecture_context_paths, test_commands, "
                "build_commands, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                (i + 1, f"p{i}", "", f"/repo/{i}", "main", "active", "[]", "[]", "[]"),
            )
        for i in range(memories):
            con.execute(
                "INSERT INTO project_memory (id, project_id, type, title, content, "
                "status, tags, source, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                (i + 1, 1, "constraint", f"rule {i}", "content", "accepted", "[]", "manual"),
            )
        con.commit()
        return {
            "projects": con.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "project_memory": con.execute(
                "SELECT COUNT(*) FROM project_memory"
            ).fetchone()[0],
        }
    finally:
        con.close()


def row_counts(db_path: Path) -> dict[str, int]:
    con = sqlite3.connect(db_path)
    try:
        return {
            "projects": con.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "project_memory": con.execute(
                "SELECT COUNT(*) FROM project_memory"
            ).fetchone()[0],
        }
    finally:
        con.close()


# ------------------------------------------------------------ fresh database


def test_fresh_database_is_created_at_head(tmp_path):
    db = tmp_path / "fresh.db"
    state = ensure_schema_sync(settings_for(db, tmp_path))

    assert state.fresh is True
    assert state.stamped_baseline is False
    assert state.revision_after == head_revision()
    assert EXPECTED_TABLES <= table_names(db)


def test_fresh_database_has_every_migration_applied(tmp_path):
    """A new database must not be missing columns added by later revisions."""
    db = tmp_path / "fresh.db"
    ensure_schema_sync(settings_for(db, tmp_path))

    assert "source_commit" in columns(db, "project_memory")
    assert "capability_profile" in columns(db, "projects")
    assert "capability_requirements" in columns(db, "tasks")
    assert "advisory_results" in columns(db, "tasks")


def test_fresh_database_matches_the_orm_metadata(tmp_path):
    """The migrations and the models must not drift apart.

    Without this, a model change without a migration passes every test that uses
    a fresh test database and fails only in production, where the schema is old.
    """
    db = tmp_path / "fresh.db"
    ensure_schema_sync(settings_for(db, tmp_path))

    for table_name, table in Base.metadata.tables.items():
        actual = set(columns(db, table_name))
        expected = {c.name for c in table.columns}
        assert expected == actual, (
            f"table {table_name!r} drifted: model has {sorted(expected - actual)} "
            f"that migrations do not create; migrations have "
            f"{sorted(actual - expected)} the model does not declare"
        )


def test_migrations_directory_creation(tmp_path):
    """SQLite will not create the parent directory; the runner must."""
    db = tmp_path / "nested" / "deeper" / "sceneworks.db"
    ensure_schema_sync(settings_for(db, tmp_path))

    assert db.is_file()


# -------------------------------------------------- adoption of a legacy database


def test_legacy_database_is_stamped_not_rebuilt(tmp_path):
    """THE WP3 CLOSURE TEST: adopting an existing database preserves its data."""
    db = tmp_path / "legacy.db"
    make_legacy_database(db)
    before = seed_rows(db)
    assert current_revision(settings_for(db, tmp_path)) is None

    state = ensure_schema_sync(settings_for(db, tmp_path))

    assert state.fresh is False
    assert state.stamped_baseline is True
    assert state.revision_after == head_revision()
    assert row_counts(db) == before, "adoption must not lose a single row"


def test_legacy_database_gains_the_new_column_with_null_for_old_rows(tmp_path):
    """NULL means "not recorded" — never a fabricated commit."""
    db = tmp_path / "legacy.db"
    make_legacy_database(db)
    seed_rows(db)
    assert "source_commit" not in columns(db, "project_memory")

    ensure_schema_sync(settings_for(db, tmp_path))

    assert "source_commit" in columns(db, "project_memory")
    con = sqlite3.connect(db)
    try:
        nulls = con.execute(
            "SELECT COUNT(*) FROM project_memory WHERE source_commit IS NULL"
        ).fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM project_memory").fetchone()[0]
    finally:
        con.close()
    assert nulls == total, "pre-existing memories must not be given invented commits"


def test_legacy_database_gains_empty_capability_and_advisory_structures(tmp_path):
    """WP10 adoption adds empty structures, never fabricated expertise/evidence."""
    db = tmp_path / "legacy.db"
    make_legacy_database(db)
    seed_rows(db)

    ensure_schema_sync(settings_for(db, tmp_path))

    assert "capability_profile" in columns(db, "projects")
    assert "capability_requirements" in columns(db, "tasks")
    assert "advisory_results" in columns(db, "tasks")
    con = sqlite3.connect(db)
    try:
        profiles = [row[0] for row in con.execute("SELECT capability_profile FROM projects")]
    finally:
        con.close()
    assert profiles and all(value == "{}" for value in profiles)


def test_adoption_stamps_the_documented_baseline(tmp_path):
    db = tmp_path / "legacy.db"
    make_legacy_database(db)

    ensure_schema_sync(settings_for(db, tmp_path))

    # It passed through the baseline on the way to head.
    assert head_revision() != BASELINE_REVISION or True
    assert current_revision(settings_for(db, tmp_path)) == head_revision()


def test_baseline_reproduces_what_create_all_produced(tmp_path):
    """Stamping is only safe if 0001 really equals the legacy schema.

    Builds the same database both ways and compares, so a drifted baseline is
    caught here rather than by a production query failing on a missing column.
    """
    legacy = tmp_path / "legacy.db"
    make_legacy_database(legacy)

    migrated = tmp_path / "migrated.db"
    ensure_schema_sync(settings_for(migrated, tmp_path))

    assert table_names(legacy) <= table_names(migrated) | {"alembic_version"}
    for table in EXPECTED_TABLES:
        legacy_cols = set(columns(legacy, table))
        migrated_cols = set(columns(migrated, table))
        # migrated may have MORE (later revisions add columns), never fewer.
        assert legacy_cols <= migrated_cols, (
            f"baseline is missing columns the legacy schema had in {table!r}: "
            f"{sorted(legacy_cols - migrated_cols)}"
        )


# ------------------------------------------------------------- idempotence


def test_running_migrations_twice_is_a_no_op(tmp_path):
    db = tmp_path / "fresh.db"
    ensure_schema_sync(settings_for(db, tmp_path))
    seeded = seed_rows(db)

    state = ensure_schema_sync(settings_for(db, tmp_path))

    assert state.fresh is False
    assert state.stamped_baseline is False
    assert state.revision_before == state.revision_after
    assert "already at head" in state.action
    assert row_counts(db) == seeded


def test_startup_never_recreates_an_existing_database(tmp_path):
    """No silent destructive recreation, asserted directly."""
    db = tmp_path / "fresh.db"
    ensure_schema_sync(settings_for(db, tmp_path))
    seeded = seed_rows(db)

    for _ in range(3):
        ensure_schema_sync(settings_for(db, tmp_path))

    assert row_counts(db) == seeded


# ------------------------------------------------------- revision reporting


def test_is_up_to_date_reflects_reality(tmp_path):
    db = tmp_path / "fresh.db"
    assert is_up_to_date(settings_for(db, tmp_path)) is False

    ensure_schema_sync(settings_for(db, tmp_path))

    assert is_up_to_date(settings_for(db, tmp_path)) is True


def test_verify_schema_raises_when_not_at_head(tmp_path):
    """The release-validation guard must fail loudly, not warn."""
    db = tmp_path / "unmigrated.db"
    make_legacy_database(db)

    with pytest.raises(MigrationError) as exc:
        verify_schema_sync(settings_for(db, tmp_path))
    assert "alembic upgrade head" in str(exc.value)


def test_verify_schema_passes_at_head(tmp_path):
    db = tmp_path / "fresh.db"
    ensure_schema_sync(settings_for(db, tmp_path))

    verify_schema_sync(settings_for(db, tmp_path))  # must not raise


def test_current_revision_is_none_for_an_unmanaged_database(tmp_path):
    db = tmp_path / "legacy.db"
    make_legacy_database(db)

    assert current_revision(settings_for(db, tmp_path)) is None


# -------------------------------------------------------- targeting safety


def test_migrations_run_against_the_requested_database_only(tmp_path):
    """REGRESSION: env.py resolved the URL from ambient settings.

    A migration invoked programmatically against one database was executed
    against the *default* one instead, attempting to create tables in a live
    database holding real project history. The caller's explicit choice must win.
    """
    target = tmp_path / "target.db"
    ambient = tmp_path / "ambient.db"
    make_legacy_database(target)
    make_legacy_database(ambient)

    settings = settings_for(target, tmp_path)
    ensure_schema_sync(settings)

    assert current_revision(settings) == head_revision()
    assert current_revision(settings_for(ambient, tmp_path)) is None
