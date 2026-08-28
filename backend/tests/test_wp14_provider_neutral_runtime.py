"""WP14 provider-neutral execution/runtime qualification."""

from __future__ import annotations

import hashlib
import sys

import pytest

from app.runtime.base import RuntimeErrorBase
from app.runtime.native import NativeRuntime


async def _rpc(client, method, params=None, request_id=1):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
    )
    return response, response.json() if response.content else None


async def _call(client, name: str, arguments: dict, request_id=1):
    response, body = await _rpc(
        client,
        "tools/call",
        {"name": name, "arguments": arguments},
        request_id=request_id,
    )
    assert response.status_code == 200, response.text
    assert body["result"]["isError"] is False, body
    return body["result"]["structuredContent"]


async def test_native_runtime_confines_paths_and_supports_optimistic_write(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    target = root / "file.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    runtime = NativeRuntime()

    read = await runtime.read_text(root, "file.txt")
    assert read["content"] == "alpha\nbeta\n"
    assert read["sha256"] == hashlib.sha256(b"alpha\nbeta\n").hexdigest()

    written = await runtime.write_text(
        root,
        "file.txt",
        "changed\n",
        expected_sha256=read["sha256"],
    )
    assert written["created"] is False
    assert target.read_text(encoding="utf-8") == "changed\n"

    with pytest.raises(RuntimeErrorBase, match="changed since it was read"):
        await runtime.write_text(
            root,
            "file.txt",
            "stale\n",
            expected_sha256=read["sha256"],
        )

    with pytest.raises(RuntimeErrorBase, match="escapes"):
        await runtime.read_text(root, "../outside.txt")

    await runtime.shutdown()


async def test_native_runtime_commands_and_persistent_processes(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    runtime = NativeRuntime()

    result = await runtime.run_command(
        root,
        sys.executable,
        ["-c", "print('direct-runtime-ok')"],
    )
    assert result.returncode == 0
    assert "direct-runtime-ok" in result.stdout

    started = await runtime.start_process(
        root,
        sys.executable,
        ["-u", "-c", "import time; print('started'); time.sleep(0.1); print('done')"],
    )
    assert started.running is True

    import asyncio

    await asyncio.sleep(0.25)
    output = await runtime.process_output(started.process_id, cursor=0, max_events=20)
    text = "".join(item["text"] for item in output.output)
    assert "started" in text
    assert "done" in text
    assert output.running is False

    await runtime.shutdown()


async def test_mcp_can_register_project_and_create_direct_engineering_session(
    client, context, git_repo
):
    context.settings.mcp_mode = "standard"
    registered = await _call(
        client,
        "sceneworks.register_project",
        {"repository_path": str(git_repo), "name": "PCS-like project"},
    )
    project_id = registered["project"]["id"]
    assert registered["project"]["already_registered"] is False
    assert registered["host_path_validated"] is True

    duplicate = await _call(
        client,
        "sceneworks.register_project",
        {"repository_path": str(git_repo)},
        request_id=2,
    )
    assert duplicate["project"]["id"] == project_id
    assert duplicate["project"]["already_registered"] is True

    context.settings.mcp_mode = "advanced"
    names_response, names_body = await _rpc(client, "tools/list", request_id=3)
    assert names_response.status_code == 200
    names = {tool["name"] for tool in names_body["result"]["tools"]}
    assert {
        "sceneworks.engineering_session.create",
        "sceneworks.workspace.read",
        "sceneworks.workspace.write",
        "sceneworks.command.run",
        "sceneworks.process.start",
        "sceneworks.git.diff",
        "sceneworks.agent.delegate",
    } <= names

    created = await _call(
        client,
        "sceneworks.engineering_session.create",
        {
            "project_id": project_id,
            "runtime": "native",
            "permissions": [
                "repository_read",
                "repository_write",
                "shell_execute",
                "process_control",
                "git_commit",
                "agent_delegate",
            ],
        },
        request_id=4,
    )
    engineering = created["session"]
    assert engineering["status"] == "ACTIVE"
    assert engineering["branch"].startswith("sw/mcp-")
    assert engineering["has_worktree"] is True
    assert "worktree_path" not in engineering

    read = await _call(
        client,
        "sceneworks.workspace.read",
        {"session_id": engineering["id"], "path": "app.py"},
        request_id=5,
    )
    assert "return 1 + 1" in read["content"]

    command = await _call(
        client,
        "sceneworks.command.run",
        {
            "session_id": engineering["id"],
            "command": sys.executable,
            "args": ["-c", "print('mcp-direct-ok')"],
        },
        request_id=6,
    )
    assert command["returncode"] == 0
    assert "mcp-direct-ok" in command["stdout"]

    closed = await _call(
        client,
        "sceneworks.engineering_session.close",
        {"session_id": engineering["id"], "cleanup_worktree": True},
        request_id=7,
    )
    assert closed["session"]["status"] == "CLOSED"
    assert closed["session"]["has_worktree"] is False
