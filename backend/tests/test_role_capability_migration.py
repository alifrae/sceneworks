"""Focused migration coverage for the WP10 capability/advisory columns."""

from __future__ import annotations

import json
import sqlite3

from alembic import command

from app.config.settings import Settings
from app.db.migrations import _alembic_config, ensure_schema_sync, head_revision


def _settings(db, tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{db.as_posix()}",
        worktree_root=tmp_path / "wt",
    )


def test_wp10_is_current_schema_head():
    assert head_revision() == "0006"


def test_0005_database_upgrades_without_fabricating_capabilities_or_evidence(tmp_path):
    db = tmp_path / "pre-wp10.db"
    settings = _settings(db, tmp_path)
    config = _alembic_config(settings.database_url)
    command.upgrade(config, "0005")

    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO projects (id, name, description, repository_path, "
            "default_branch, status, architecture_context_paths, test_commands, "
            "build_commands, created_at, updated_at) "
            "VALUES (1,'existing','','/repo','main','active','[]','[]','[]',"
            "datetime('now'),datetime('now'))"
        )
        con.execute(
            "INSERT INTO tasks (id, project_id, title, description, status, priority, "
            "engineering_contract, changed_files, created_at, updated_at) "
            "VALUES (1,1,'existing task','','NEW','medium','{}','[]',"
            "datetime('now'),datetime('now'))"
        )
        con.commit()
    finally:
        con.close()

    state = ensure_schema_sync(settings)
    assert state.revision_after == "0006"

    con = sqlite3.connect(db)
    try:
        project_profile = con.execute(
            "SELECT capability_profile FROM projects WHERE id=1"
        ).fetchone()[0]
        task_profile, advisory = con.execute(
            "SELECT capability_requirements, advisory_results FROM tasks WHERE id=1"
        ).fetchone()
    finally:
        con.close()

    assert json.loads(project_profile) == {}
    assert json.loads(task_profile) == {}
    assert json.loads(advisory) == {}
