"""WP13 MCP coverage for lightweight work-management metadata."""

from __future__ import annotations


async def _rpc(client, method: str, params: dict | None = None, request_id: int = 1):
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )
    return response, response.json()


async def test_mcp_create_and_read_preserve_work_item_intent(client, context, git_repo):
    project_response = await client.post(
        "/api/projects",
        json={"name": "wp13-mcp", "repository_path": str(git_repo)},
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]
    context.settings.mcp_allow_actions = True

    response, body = await _rpc(
        client,
        "tools/call",
        {
            "name": "sceneworks.create_task",
            "arguments": {
                "project_id": project_id,
                "title": "Diagnose playback freeze",
                "work_item_type": "bug",
                "requested_mode": "investigate",
            },
        },
    )
    assert response.status_code == 200
    result = body["result"]
    assert result["isError"] is False
    created = result["structuredContent"]["task"]
    assert created["status"] == "NEW"
    assert created["work_item_type"] == "bug"
    assert created["requested_mode"] == "investigate"
    assert created["resolved_mode"] == "investigate"
    task_id = created["id"]

    _, body = await _rpc(
        client,
        "tools/call",
        {"name": "sceneworks.get_task", "arguments": {"task_id": task_id}},
        request_id=2,
    )
    task = body["result"]["structuredContent"]["task"]
    assert task["work_item_type"] == "bug"
    assert task["requested_mode"] == "investigate"
    assert task["resolved_mode"] == "investigate"
    assert task["effective_mode"] == "investigate"
