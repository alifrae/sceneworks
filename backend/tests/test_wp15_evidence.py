"""WP15 EngineeringSession turn/evidence regression tests."""

from __future__ import annotations

import asyncio
import sys


async def _rpc(client, name: str, arguments: dict, request_id: int = 1):
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
    body = response.json()
    result = body["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


async def _register_project_and_task(client, git_repo):
    project_response = await client.post(
        "/api/projects",
        json={
            "name": "wp15-demo",
            "description": "evidence test",
            "repository_path": str(git_repo),
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    task_response = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "title": "Verify evidence correlation",
        },
    )
    assert task_response.status_code == 201, task_response.text
    return project, task_response.json()


async def test_wp15_task_bound_turn_records_file_command_process_and_git_evidence(
    client, context, git_repo
):
    project, task = await _register_project_and_task(client, git_repo)
    context.settings.mcp_mode = "advanced"

    created = await _rpc(
        client,
        "sceneworks.engineering_session.create",
        {"project_id": project["id"], "task_id": task["id"]},
    )
    session = created["session"]
    assert session["task_id"] == task["id"]
    session_id = session["id"]
    assert created["evidence_action_id"]

    begun = await _rpc(
        client,
        "sceneworks.engineering_session.begin_turn",
        {"session_id": session_id, "intent": "reproduce then verify"},
        request_id=2,
    )
    turn_id = begun["turn"]["id"]
    assert begun["turn"]["task_id"] == task["id"]

    read = await _rpc(
        client,
        "sceneworks.workspace.read",
        {"session_id": session_id, "turn_id": turn_id, "path": "README.md"},
        request_id=3,
    )
    original_hash = read["sha256"]
    assert read["evidence_action_id"]

    written = await _rpc(
        client,
        "sceneworks.workspace.write",
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "path": "README.md",
            "content": "# WP15 changed\n",
            "expected_sha256": original_hash,
        },
        request_id=4,
    )
    assert written["sha256"] != original_hash

    command = await _rpc(
        client,
        "sceneworks.command.run",
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "command": sys.executable,
            "args": ["-c", "print('cmd-evidence')"],
            "timeout": 30,
        },
        request_id=5,
    )
    assert command["returncode"] == 0
    assert "cmd-evidence" in command["stdout"]

    process = await _rpc(
        client,
        "sceneworks.process.start",
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "command": sys.executable,
            "args": [
                "-u",
                "-c",
                "import time; print('process-evidence', flush=True); time.sleep(60)",
            ],
        },
        request_id=6,
    )
    process_id = process["process"]["process_id"]
    assert process["process"]["pid"]
    assert process["process"]["started_at"]

    await asyncio.sleep(0.1)
    output = await _rpc(
        client,
        "sceneworks.process.output",
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "process_id": process_id,
            "cursor": 0,
        },
        request_id=7,
    )
    assert output["process"]["process_id"] == process_id

    stopped = await _rpc(
        client,
        "sceneworks.process.stop",
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "process_id": process_id,
        },
        request_id=8,
    )
    assert stopped["process"]["running"] is False
    assert stopped["process"]["finished_at"]

    diff = await _rpc(
        client,
        "sceneworks.git.diff",
        {"session_id": session_id, "turn_id": turn_id},
        request_id=9,
    )
    readme = next(row for row in diff["changed_files"] if row["path"] == "README.md")
    assert readme["sha256"] == written["sha256"]

    evidence = await _rpc(
        client,
        "sceneworks.engineering_session.evidence",
        {"session_id": session_id, "turn_id": turn_id, "limit": 100},
        request_id=10,
    )
    rows = evidence["evidence"]
    assert rows
    assert all(row["task_id"] == task["id"] for row in rows)
    assert all(row["turn_id"] == turn_id for row in rows)
    categories = {row["category"] for row in rows}
    assert {"file", "command", "process", "git"} <= categories

    file_write = next(row for row in rows if row["operation"] == "workspace.write")
    assert file_write["payload"]["sha256_before"] == original_hash
    assert file_write["payload"]["sha256_after"] == written["sha256"]
    assert "content" not in file_write["payload"]

    command_row = next(row for row in rows if row["operation"] == "command.run")
    assert command_row["payload"]["exit_code"] == 0
    assert "cmd-evidence" in command_row["payload"]["stdout"]
    assert command_row["started_at"]
    assert command_row["finished_at"]

    git_row = next(row for row in rows if row["operation"] == "git.diff")
    assert "working" not in git_row["payload"]
    assert git_row["payload"]["working_sha256"]
    assert git_row["payload"]["changed_files"][0]["sha256"]

    summary = await _rpc(
        client,
        "sceneworks.engineering_session.summary",
        {"session_id": session_id},
        request_id=11,
    )
    assert summary["task_id"] == task["id"]
    assert summary["turn_count"] == 1
    assert summary["evidence_count"] >= len(rows)
    assert summary["failure_count"] == 0

    finished = await _rpc(
        client,
        "sceneworks.engineering_session.finish_turn",
        {"session_id": session_id, "turn_id": turn_id, "status": "COMPLETED"},
        request_id=12,
    )
    assert finished["turn"]["status"] == "COMPLETED"


async def test_wp15_tool_catalog_exposes_turns_evidence_and_task_binding(
    client, context
):
    context.settings.mcp_mode = "advanced"
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert "sceneworks.engineering_session.begin_turn" in tools
    assert "sceneworks.engineering_session.events" in tools
    assert "sceneworks.engineering_session.evidence" in tools
    assert "sceneworks.engineering_session.summary" in tools
    create_schema = tools["sceneworks.engineering_session.create"]["inputSchema"]
    assert "task_id" in create_schema["properties"]
    command_schema = tools["sceneworks.command.run"]["inputSchema"]
    assert "turn_id" in command_schema["properties"]
