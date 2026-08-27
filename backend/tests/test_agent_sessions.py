"""WP11 Advanced-mode persistent Gemini ACP session tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.services.agent_sessions import AgentSessionError

MOCK = Path(__file__).parent / "mock_acp_server.py"


def _mock_executable(tmp_path: Path) -> str:
    python = sys.executable
    if sys.platform == "win32":
        wrapper = tmp_path / "mock-gemini-advanced.cmd"
        wrapper.write_text(
            f'@echo off\n"{python}" "{MOCK}" %*\n', encoding="utf-8"
        )
    else:
        wrapper = tmp_path / "mock-gemini-advanced.sh"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{python}" "{MOCK}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    return str(wrapper)


async def _register_project(client, git_repo):
    response = await client.post(
        "/api/projects",
        json={
            "name": "advanced-demo",
            "description": "Advanced MCP session test",
            "repository_path": str(git_repo),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _wait_session(context, session_id: int, terminal=("ACTIVE", "FAILED"), timeout=8):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        row = await context.agent_sessions.get(session_id)
        if row.status in terminal:
            return row
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"advanced session {session_id} never reached {terminal}: {row.status}"
            )
        await asyncio.sleep(0.05)


async def _rpc(client, name: str, arguments: dict, request_id=1):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def test_advanced_session_create_and_repeated_prompt_loads_provider_context(
    client, context, git_repo, tmp_path
):
    project = await _register_project(client, git_repo)
    context.settings.gemini_executable = _mock_executable(tmp_path)
    context.settings.mcp_mode = "advanced"
    context.settings.mcp_allow_actions = False

    result = await _rpc(
        client,
        "sceneworks.agent_session.create",
        {
            "project_id": project["id"],
            "permissions": ["repository_read"],
        },
    )
    assert result["isError"] is False
    session = result["structuredContent"]["session"]
    session_id = session["id"]
    assert session["status"] == "ACTIVE"
    assert session["branch"] is None
    assert session["provider_session_persisted"] is True
    assert "provider_session_id" not in session
    assert session["provider_capabilities"]["agent_capabilities"]["loadSession"] is True

    first = await _rpc(
        client,
        "sceneworks.agent_session.prompt",
        {"session_id": session_id, "prompt": "Inspect app.py."},
        request_id=2,
    )
    assert first["isError"] is False
    assert first["structuredContent"]["session"]["status"] == "RUNNING"
    row = await _wait_session(context, session_id)
    assert row.status == "ACTIVE"
    assert "VERDICT: APPROVED" in (row.last_result or "")

    second = await _rpc(
        client,
        "sceneworks.agent_session.prompt",
        {"session_id": session_id, "prompt": "Now verify that conclusion."},
        request_id=3,
    )
    assert second["isError"] is False
    row = await _wait_session(context, session_id)
    assert row.status == "ACTIVE"
    assert "VERDICT: APPROVED" in (row.last_result or "")

    closed = await _rpc(
        client,
        "sceneworks.agent_session.close",
        {"session_id": session_id},
        request_id=4,
    )
    assert closed["isError"] is False
    assert closed["structuredContent"]["session"]["status"] == "CLOSED"


async def test_advanced_write_session_isolated_and_diff_reports_worktree_state(
    client, context, git_repo, tmp_path, monkeypatch
):
    project = await _register_project(client, git_repo)
    context.settings.gemini_executable = _mock_executable(tmp_path)
    context.settings.mcp_mode = "advanced"
    monkeypatch.setenv("MOCK_ACP_WRITE_MODE", "1")

    created = await _rpc(
        client,
        "sceneworks.agent_session.create",
        {
            "project_id": project["id"],
            "permissions": ["repository_read", "repository_write"],
        },
    )
    session = created["structuredContent"]["session"]
    assert session["branch"].startswith("sw-agent-session-")
    session_id = session["id"]

    await _rpc(
        client,
        "sceneworks.agent_session.prompt",
        {"session_id": session_id, "prompt": "Create the requested file."},
        request_id=2,
    )
    row = await _wait_session(context, session_id)
    assert row.status == "ACTIVE"
    assert row.worktree_path
    assert (Path(row.worktree_path) / "mock.txt").read_text(encoding="utf-8") == (
        "created by mock agent\n"
    )
    # Human checkout remains untouched.
    assert not (git_repo / "mock.txt").exists()

    diff = await _rpc(
        client,
        "sceneworks.agent_session.diff",
        {"session_id": session_id},
        request_id=3,
    )
    assert diff["isError"] is False
    assert "mock.txt" in diff["structuredContent"]["status"]


async def test_advanced_permission_allowlist_cannot_be_exceeded(
    client, context, git_repo, tmp_path
):
    project = await _register_project(client, git_repo)
    context.settings.gemini_executable = _mock_executable(tmp_path)
    context.settings.mcp_mode = "advanced"
    context.settings.advanced_session_permissions = ["repository_read"]

    result = await _rpc(
        client,
        "sceneworks.agent_session.create",
        {
            "project_id": project["id"],
            "permissions": ["repository_read", "shell_execute"],
        },
    )
    assert result["isError"] is True
    assert "disabled by SceneWorks settings" in result["structuredContent"]["error"]


async def test_persistent_session_requires_gemini_load_session_capability(
    client, context, git_repo, tmp_path, monkeypatch
):
    project = await _register_project(client, git_repo)
    context.settings.gemini_executable = _mock_executable(tmp_path)
    context.settings.mcp_mode = "advanced"
    monkeypatch.setenv("MOCK_ACP_NO_LOAD_SESSION", "1")

    created = await _rpc(
        client,
        "sceneworks.agent_session.create",
        {"project_id": project["id"], "permissions": ["repository_read"]},
    )
    # Creation records provider capabilities; the first prompt must fail closed
    # rather than silently becoming a context-less one-shot execution.
    session_id = created["structuredContent"]["session"]["id"]
    await _rpc(
        client,
        "sceneworks.agent_session.prompt",
        {"session_id": session_id, "prompt": "Continue the prior conversation."},
        request_id=2,
    )
    row = await _wait_session(context, session_id)
    assert row.status == "FAILED"
    assert "loadSession" in (row.last_error or "")


async def test_advanced_tools_are_rejected_outside_advanced_mode(
    client, context, git_repo
):
    project = await _register_project(client, git_repo)
    context.settings.mcp_mode = "standard"
    context.settings.mcp_allow_actions = False
    result = await _rpc(
        client,
        "sceneworks.agent_session.create",
        {"project_id": project["id"]},
    )
    assert result["isError"] is True
    assert "requires explicit Advanced MCP mode" in result["structuredContent"]["error"]
