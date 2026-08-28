"""OpenCode non-ACP backup-backend contract tests."""

from __future__ import annotations

import sys
from pathlib import Path

from app.agents.base import AgentEventSink, AgentRequest, Workspace
from app.agents.opencode import OpenCodeBackend
from app.config.settings import Settings


async def _emit(*args, **kwargs):
    del args, kwargs


async def test_opencode_health_is_unavailable_when_executable_missing(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}",
        opencode_executable=str(tmp_path / "missing-opencode"),
    )
    backend = OpenCodeBackend(settings)
    health = await backend.health()
    assert health.key == "opencode"
    assert health.available is False
    assert "not found" in (health.detail or "").lower()


async def test_opencode_refuses_read_only_execution_before_launch(tmp_path):
    # sys.executable is intentionally used as a known executable. The policy
    # rejection must happen before it is invoked as though it were OpenCode.
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}",
        opencode_executable=sys.executable,
    )
    backend = OpenCodeBackend(settings)
    root = tmp_path / "worktree"
    root.mkdir()
    sink = AgentEventSink("exec-1", None, _emit)
    result = await backend.run(
        AgentRequest(
            execution_id="exec-1",
            role="architect",
            system_prompt="",
            user_prompt="inspect only",
        ),
        Workspace(
            path=Path(root),
            repo_path=Path(root),
            permissions=("repository_read",),
        ),
        sink,
    )
    assert result.status == "failed"
    assert "restricted to write-capable" in (result.error or "")
