"""Gemini ACP backend tests against a mock ACP server.

No live Gemini access required. The mock speaks the same ACP v1 protocol
subset as Gemini CLI 0.53.x (see mock_acp_server.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.agents.base import AgentEventSink, AgentRequest, Workspace
from app.agents.gemini_acp import GeminiACPBackend
from app.config.settings import Settings

MOCK = Path(__file__).parent / "mock_acp_server.py"


@pytest.fixture
def mock_executable(tmp_path: Path) -> str:
    """A wrapper executable that runs the mock ACP server (works on win32)."""
    python = sys.executable
    if sys.platform == "win32":
        wrapper = tmp_path / "mock-gemini.cmd"
        wrapper.write_text(
            f'@echo off\n"{python}" "{MOCK}" %*\n', encoding="utf-8"
        )
    else:
        wrapper = tmp_path / "mock-gemini.sh"
        wrapper.write_text(f'#!/bin/sh\nexec "{python}" "{MOCK}" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    return str(wrapper)


def make_settings(tmp_path: Path, mock: str, **extra) -> Settings:
    values = dict(
        gemini_executable=mock,
        gemini_startup_timeout_seconds=15,
        execution_timeout_seconds=60,
        log_level="WARNING",
        roles_dir=Path(__file__).resolve().parent.parent / "app" / "roles" / "prompts",
        worktree_root=tmp_path / "wt",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}",
    )
    values.update(extra)
    return Settings(**values)


class RecordingSink(AgentEventSink):
    def __init__(self):
        super().__init__("test-exec", None, lambda *args: _noop())
        self.events: list[tuple[str, dict, str]] = []

    async def emit(self, type: str, payload: dict, severity: str = "info") -> None:
        self.events.append((type, payload, severity))


async def _noop():
    pass


async def test_mock_acp_happy_path(tmp_path, mock_executable):
    settings = make_settings(tmp_path, mock_executable)
    backend = GeminiACPBackend(settings)
    workspace = Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",))
    sink = RecordingSink()
    request = AgentRequest(
        execution_id="test-1",
        role="architect",
        system_prompt="You are read-only.",
        user_prompt="Analyze the code.",
    )
    result = await backend.run(request, workspace, sink)
    assert result.status == "completed"
    summary = result.summary or ""
    assert "VERDICT: APPROVED" in summary
    types = [t for t, _, _ in sink.events]
    assert "agent.text_delta" in types
    assert "agent.thought_summary" in types
    assert "tool.started" in types


async def test_mock_acp_write_denied_for_read_only_role(tmp_path, mock_executable):
    settings = make_settings(tmp_path, mock_executable)
    os.environ["MOCK_ACP_WRITE_MODE"] = "1"
    try:
        backend = GeminiACPBackend(settings)
        workspace = Workspace(
            path=tmp_path, repo_path=tmp_path, permissions=("repository_read",)
        )
        sink = RecordingSink()
        request = AgentRequest(
            execution_id="test-2",
            role="architect",
            system_prompt="read-only",
            user_prompt="Inspect.",
        )
        result = await backend.run(request, workspace, sink)
        assert result.status == "completed"
        # The agent's write request must have been refused by the client.
        assert not (tmp_path / "mock.txt").exists()
        assert any(t == "file.changed" for t, _, _ in sink.events) is False
        denied = [p for t, p, _ in sink.events if t == "agent.event" and p.get("name") == "fs_write_denied"]
        assert denied, "expected fs_write_denied event"
    finally:
        os.environ.pop("MOCK_ACP_WRITE_MODE", None)


async def test_mock_acp_write_allowed_for_engineer(tmp_path, mock_executable):
    settings = make_settings(tmp_path, mock_executable)
    os.environ["MOCK_ACP_WRITE_MODE"] = "1"
    try:
        backend = GeminiACPBackend(settings)
        workspace = Workspace(
            path=tmp_path,
            repo_path=tmp_path,
            permissions=("repository_read", "repository_write", "shell_execute", "git_commit"),
        )
        sink = RecordingSink()
        request = AgentRequest(
            execution_id="test-3",
            role="engineer",
            system_prompt="you may edit",
            user_prompt="Implement.",
        )
        result = await backend.run(request, workspace, sink)
        assert result.status == "completed"
        assert (tmp_path / "mock.txt").read_text() == "created by mock agent\n"
        assert any(t == "file.changed" for t, _, _ in sink.events)
    finally:
        os.environ.pop("MOCK_ACP_WRITE_MODE", None)


async def test_mock_acp_refusal_is_recoverable_failure(tmp_path, mock_executable):
    settings = make_settings(tmp_path, mock_executable)
    os.environ["MOCK_ACP_STOP"] = "refusal"
    try:
        backend = GeminiACPBackend(settings)
        workspace = Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",))
        sink = RecordingSink()
        result = await backend.run(
            AgentRequest("test-4", "architect", "sys", "do it"), workspace, sink
        )
        assert result.status in ("completed", "failed")
    finally:
        os.environ.pop("MOCK_ACP_STOP", None)


async def test_mock_acp_cancellation(tmp_path, mock_executable):
    settings = make_settings(tmp_path, mock_executable)
    backend = GeminiACPBackend(settings)
    workspace = Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",))
    sink = RecordingSink()
    request = AgentRequest(
        execution_id="test-5",
        role="architect",
        system_prompt="sys",
        user_prompt="analyze",
    )
    run_task = __import__("asyncio").create_task(backend.run(request, workspace, sink))
    await __import__("asyncio").sleep(0.1)
    await backend.cancel("test-5")
    sink.cancel()
    result = await run_task
    assert result.status == "cancelled"


async def test_missing_executable_reports_clear_error(tmp_path, mock_executable):
    settings = make_settings(tmp_path, "definitely-missing-gemini-binary-xyz")
    backend = GeminiACPBackend(settings)
    health = await backend.health()
    assert health.available is False
    assert "Gemini CLI" in (health.detail or "")


async def test_health_with_mock(tmp_path, mock_executable):
    settings = make_settings(tmp_path, mock_executable)
    backend = GeminiACPBackend(settings)
    health = await backend.health()
    assert health.available is True
    assert "0.53.1" in (health.version or "") or health.version is not None
