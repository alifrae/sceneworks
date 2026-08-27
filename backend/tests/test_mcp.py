"""WP11 MCP protocol, operating-mode and semantic-boundary tests."""

from __future__ import annotations

import asyncio

from app.agents.fake import FakeAgentBackend, ScriptStep


async def _register_project(client, git_repo):
    response = await client.post(
        "/api/projects",
        json={
            "name": "mcp-demo",
            "description": "MCP test project",
            "repository_path": str(git_repo),
            "test_commands": ["pytest -q"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _rpc(client, method, params=None, request_id=1, headers=None):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
        headers=headers or {},
    )
    return response, response.json() if response.content else None


async def _tool_names(client):
    response, body = await _rpc(client, "tools/list")
    assert response.status_code == 200
    return {tool["name"] for tool in body["result"]["tools"]}


async def test_mcp_get_info_is_data_free(client):
    response = await client.get("/mcp")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "SceneWorks"
    assert body["endpoint"] == "/mcp"
    assert body["mode"] == "observe"
    assert body["action_tools_enabled"] is False
    assert body["advanced_agent_sessions_enabled"] is False
    assert "project" not in body


async def test_modern_discovery_and_legacy_initialize(client):
    response, body = await _rpc(
        client,
        "server/discover",
        headers={
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "server/discover",
        },
    )
    assert response.status_code == 200
    result = body["result"]
    assert result["resultType"] == "complete"
    assert "2026-07-28" in result["supportedVersions"]
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["ttlMs"] > 0
    assert "Observe mode" in result["instructions"]

    response, body = await _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "clientInfo": {"name": "test", "version": "1"},
        },
        request_id=2,
    )
    assert response.status_code == 200
    assert body["result"]["protocolVersion"] == "2025-11-25"
    assert body["result"]["serverInfo"]["name"] == "SceneWorks"


async def test_tool_catalog_is_semantic_and_mode_scoped(client, context):
    names = await _tool_names(client)
    read_tools = {
        "sceneworks.capabilities",
        "sceneworks.list_projects",
        "sceneworks.get_project_context",
        "sceneworks.list_tasks",
        "sceneworks.get_task",
        "sceneworks.get_task_diff",
        "sceneworks.get_execution",
        "sceneworks.search_memory",
        "sceneworks.list_artifacts",
    }
    assert read_tools <= names
    assert "sceneworks.create_task" not in names
    assert not any(name.startswith("sceneworks.agent_session.") for name in names)

    # Backward-compatible prototype switch maps Observe -> effective Standard.
    context.settings.mcp_allow_actions = True
    standard_names = await _tool_names(client)
    assert "sceneworks.inspect_repository" in standard_names
    assert "sceneworks.create_task" in standard_names
    assert "sceneworks.task_action" in standard_names
    assert not any(
        name.startswith("sceneworks.agent_session.") for name in standard_names
    )

    context.settings.mcp_allow_actions = False
    context.settings.mcp_mode = "advanced"
    advanced_names = await _tool_names(client)
    assert "sceneworks.create_task" in advanced_names
    assert "sceneworks.agent_session.create" in advanced_names
    assert "sceneworks.agent_session.prompt" in advanced_names
    assert "sceneworks.agent_session.diff" in advanced_names

    # Raw machine primitives are absent in every mode.
    for catalog in (names, standard_names, advanced_names):
        assert all("raw_shell" not in name for name in catalog)
        assert all("write_file" not in name for name in catalog)
        assert all("read_file" not in name for name in catalog)
        assert all("sql" not in name for name in catalog)
        assert all("git_command" not in name for name in catalog)


async def test_read_tools_ground_project_and_task_state(client, git_repo):
    project = await _register_project(client, git_repo)

    response, body = await _rpc(
        client,
        "tools/call",
        {"name": "sceneworks.list_projects", "arguments": {}},
    )
    assert response.status_code == 200
    assert body["result"]["isError"] is False
    assert any(
        project_row["id"] == project["id"]
        for project_row in body["result"]["structuredContent"]["projects"]
    )

    response, body = await _rpc(
        client,
        "tools/call",
        {
            "name": "sceneworks.get_project_context",
            "arguments": {"project_id": project["id"]},
        },
        request_id=2,
    )
    assert response.status_code == 200
    context = body["result"]["structuredContent"]
    assert context["project"]["name"] == "mcp-demo"
    assert context["repository_snapshot"]["is_git"] is True
    assert context["repository_snapshot"]["head_commit"]
    assert context["project"]["test_commands"] == ["pytest -q"]


async def test_action_tools_fail_closed_by_default(client, git_repo):
    project = await _register_project(client, git_repo)
    response, body = await _rpc(
        client,
        "tools/call",
        {
            "name": "sceneworks.create_task",
            "arguments": {
                "project_id": project["id"],
                "title": "should not be created",
            },
        },
    )
    assert response.status_code == 200
    assert body["result"]["isError"] is True
    assert "Standard or Advanced MCP mode" in body["result"]["structuredContent"]["error"]


async def test_enabled_actions_create_task_and_expose_allowed_actions(
    client, context, git_repo
):
    project = await _register_project(client, git_repo)
    context.settings.mcp_allow_actions = True
    response, body = await _rpc(
        client,
        "tools/call",
        {
            "name": "sceneworks.create_task",
            "arguments": {
                "project_id": project["id"],
                "title": "Fix bounded defect",
                "description": "A deterministic regression in app.py",
                "priority": "low",
                "engineering_contract": {
                    "allowed_scope": ["app.py"],
                    "required_tests": ["pytest -q"],
                    "acceptance_criteria": ["regression test passes"],
                },
            },
        },
    )
    assert response.status_code == 200
    result = body["result"]
    assert result["isError"] is False
    task_id = result["structuredContent"]["task"]["id"]

    _, body = await _rpc(
        client,
        "tools/call",
        {"name": "sceneworks.get_task", "arguments": {"task_id": task_id}},
        request_id=2,
    )
    task = body["result"]["structuredContent"]["task"]
    assert task["status"] == "NEW"
    assert "start_architecture" in task["allowed_actions"]
    assert task["engineering_contract"]["allowed_scope"] == ["app.py"]


async def test_inspect_repository_uses_technical_expert_execution(
    client, context, git_repo
):
    project = await _register_project(client, git_repo)
    context.settings.mcp_allow_actions = True
    backend = FakeAgentBackend(
        role_scripts={
            "technical_expert": [
                ScriptStep(kind="summary", summary="app.py returns 1 + 1")
            ],
        }
    )
    context.backends._backends["fake"] = backend

    response, body = await _rpc(
        client,
        "tools/call",
        {
            "name": "sceneworks.inspect_repository",
            "arguments": {
                "project_id": project["id"],
                "question": "What does app.py do?",
            },
        },
    )
    assert response.status_code == 200
    execution_id = body["result"]["structuredContent"]["execution"]["id"]
    assert execution_id

    deadline = asyncio.get_event_loop().time() + 5
    while True:
        _, poll = await _rpc(
            client,
            "tools/call",
            {
                "name": "sceneworks.get_execution",
                "arguments": {"execution_id": execution_id},
            },
            request_id=2,
        )
        execution = poll["result"]["structuredContent"]["execution"]
        if execution["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(
                f"inspection execution did not finish: {execution['status']}"
            )
        await asyncio.sleep(0.05)

    assert execution["status"] == "COMPLETED"
    assert execution["role"] == "technical_expert"
    assert "app.py returns 1 + 1" in execution["result"]
    assert set(execution["workspace"]) == {
        "branch",
        "base_commit",
        "permissions",
        "project_id",
    }


async def test_mcp_routing_headers_are_validated(client):
    response, body = await _rpc(
        client,
        "tools/list",
        headers={"Mcp-Method": "tools/call"},
    )
    assert response.status_code == 400
    assert "Mcp-Method" in body["error"]["message"]

    response, body = await _rpc(
        client,
        "tools/list",
        request_id=2,
        headers={"MCP-Protocol-Version": "2099-01-01"},
    )
    assert response.status_code == 400
    assert "unsupported MCP protocol" in body["error"]["message"]
