"""Task attachment storage, transport and provider-boundary tests."""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.agents.base import AgentAttachment, AgentRequest
from app.agents.gemini_acp import AcpError
from app.agents.gemini_acp_attachments import build_attachment_prompt_blocks
from app.models import Task, TaskAttachment


async def _project(client, git_repo):
    response = await client.post(
        "/api/projects",
        json={"name": "attachments", "repository_path": str(git_repo)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _task(client, project_id: int):
    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Diagnose screenshot defect",
            "description": "Use the attached evidence.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


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
    return response.json()["result"]


async def test_attachment_rest_round_trip_is_immutable_task_context(
    client, context, git_repo
):
    project = await _project(client, git_repo)
    task = await _task(client, project["id"])
    payload = b"renderer warning: mirror scalar map stalled\n"

    response = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={
            "filename": "renderer.log.txt",
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "source": "web",
        },
    )
    assert response.status_code == 201, response.text
    attachment = response.json()
    assert attachment["filename"] == "renderer.log.txt"
    assert attachment["mime_type"] == "text/plain"
    assert attachment["size_bytes"] == len(payload)
    assert len(attachment["sha256"]) == 64
    assert "storage_key" not in attachment

    response = await client.get(f"/api/tasks/{task['id']}/attachments")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [attachment["id"]]

    response = await client.get(
        f"/api/tasks/{task['id']}/attachments/{attachment['id']}/content"
    )
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].startswith("text/plain")

    # The stored bytes are SceneWorks-owned and never copied into the managed repo.
    assert not (git_repo / "renderer.log.txt").exists()
    async with context.engine_factory() as session:
        row = await session.get(TaskAttachment, attachment["id"])
        assert row is not None
        assert row.storage_key.startswith(f"{project['id']}/{task['id']}/")

    duplicate = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={
            "filename": "same-content.txt",
            "data_base64": base64.b64encode(payload).decode("ascii"),
        },
    )
    assert duplicate.status_code == 409

    removed = await client.delete(
        f"/api/tasks/{task['id']}/attachments/{attachment['id']}"
    )
    assert removed.status_code == 204
    assert (await client.get(f"/api/tasks/{task['id']}/attachments")).json() == []


async def test_attachment_validation_and_freeze_after_task_start(
    client, context, git_repo
):
    project = await _project(client, git_repo)
    task = await _task(client, project["id"])

    unsupported = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={
            "filename": "payload.exe",
            "data_base64": base64.b64encode(b"not executable").decode("ascii"),
        },
    )
    assert unsupported.status_code == 400
    assert "unsupported attachment type" in unsupported.text

    invalid = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={"filename": "context.txt", "data_base64": "not/base64***"},
    )
    assert invalid.status_code == 400

    context.settings.attachment_max_bytes = 3
    too_large = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={
            "filename": "context.txt",
            "data_base64": base64.b64encode(b"four").decode("ascii"),
        },
    )
    assert too_large.status_code == 413
    context.settings.attachment_max_bytes = 20_000_000

    created = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={
            "filename": "context.txt",
            "data_base64": base64.b64encode(b"context").decode("ascii"),
        },
    )
    assert created.status_code == 201

    async with context.engine_factory() as session:
        row = await session.get(Task, task["id"])
        assert row is not None
        row.status = "ARCHITECTURE_ANALYSIS"
        await session.commit()

    frozen_add = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={
            "filename": "late.txt",
            "data_base64": base64.b64encode(b"late").decode("ascii"),
        },
    )
    assert frozen_add.status_code == 409
    frozen_delete = await client.delete(
        f"/api/tasks/{task['id']}/attachments/{created.json()['id']}"
    )
    assert frozen_delete.status_code == 409


async def test_mcp_attachment_tools_return_rich_content(client, context, git_repo):
    project = await _project(client, git_repo)
    task = await _task(client, project["id"])

    # Observe mode exposes read tools but not attachment mutation.
    catalog = (
        await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
    ).json()["result"]["tools"]
    names = {tool["name"] for tool in catalog}
    assert "sceneworks.list_task_attachments" in names
    assert "sceneworks.get_task_attachment" in names
    assert "sceneworks.add_task_attachment" not in names

    context.settings.mcp_allow_actions = True
    image = b"\x89PNG\r\n\x1a\nmock-image"
    added = await _rpc(
        client,
        "sceneworks.add_task_attachment",
        {
            "task_id": task["id"],
            "filename": "freeze.png",
            "data_base64": base64.b64encode(image).decode("ascii"),
        },
    )
    assert added["isError"] is False
    attachment = added["structuredContent"]["attachment"]
    assert attachment["source"] == "mcp"
    assert attachment["authority"] == "untrusted_user_context"

    listed = await _rpc(
        client,
        "sceneworks.list_task_attachments",
        {"task_id": task["id"]},
        request_id=2,
    )
    assert listed["structuredContent"]["attachments"][0]["id"] == attachment["id"]

    task_result = await _rpc(
        client, "sceneworks.get_task", {"task_id": task["id"]}, request_id=3
    )
    assert task_result["structuredContent"]["task"]["attachments"][0]["filename"] == "freeze.png"

    rich = await _rpc(
        client,
        "sceneworks.get_task_attachment",
        {"attachment_id": attachment["id"]},
        request_id=4,
    )
    assert rich["isError"] is False
    blocks = rich["content"]
    assert blocks[1]["type"] == "image"
    assert blocks[1]["mimeType"] == "image/png"
    assert base64.b64decode(blocks[1]["data"]) == image
    assert "untrusted" in rich["structuredContent"]["authority_note"].lower()


async def test_mcp_attachment_upload_limit_is_smaller_than_local_limit(
    client, context, git_repo
):
    project = await _project(client, git_repo)
    task = await _task(client, project["id"])
    context.settings.mcp_allow_actions = True
    context.settings.mcp_attachment_max_bytes = 3
    result = await _rpc(
        client,
        "sceneworks.add_task_attachment",
        {
            "task_id": task["id"],
            "filename": "too-big.txt",
            "data_base64": base64.b64encode(b"four").decode("ascii"),
        },
    )
    assert result["isError"] is True
    assert "MCP 3 byte limit" in result["structuredContent"]["error"]


def test_gemini_acp_builds_multimodal_blocks_and_marks_context_untrusted():
    request = AgentRequest(
        execution_id="e1",
        role="architect",
        system_prompt="Inspect only.",
        user_prompt="Explain the freeze.",
        attachments=(
            AgentAttachment(
                id=11,
                filename="freeze.png",
                mime_type="image/png",
                size_bytes=3,
                sha256="a" * 64,
                data=b"png",
            ),
            AgentAttachment(
                id=12,
                filename="requirement.pdf",
                mime_type="application/pdf",
                size_bytes=3,
                sha256="b" * 64,
                data=b"pdf",
            ),
            AgentAttachment(
                id=13,
                filename="trace.txt",
                mime_type="text/plain",
                size_bytes=5,
                sha256="c" * 64,
                data=b"trace",
            ),
        ),
        metadata={"task_id": 7},
    )
    blocks = build_attachment_prompt_blocks(
        request, {"image": True, "embeddedContext": True}
    )
    assert blocks[0]["type"] == "text"
    assert "untrusted" in blocks[0]["text"].lower()
    assert blocks[1]["type"] == "image"
    assert blocks[1]["mimeType"] == "image/png"
    assert blocks[2]["type"] == "resource"
    assert blocks[2]["resource"]["mimeType"] == "application/pdf"
    assert base64.b64decode(blocks[2]["resource"]["blob"]) == b"pdf"
    assert blocks[3]["resource"]["text"] == "trace"
    assert blocks[3]["resource"]["uri"].startswith("sceneworks://task/7/")


def test_gemini_acp_fails_closed_when_binary_capability_is_missing():
    request = AgentRequest(
        execution_id="e2",
        role="architect",
        system_prompt="Inspect.",
        user_prompt="Analyze.",
        attachments=(
            AgentAttachment(
                id=1,
                filename="requirement.pdf",
                mime_type="application/pdf",
                size_bytes=3,
                sha256="d" * 64,
                data=b"pdf",
            ),
        ),
    )
    with pytest.raises(AcpError, match="embedded-context"):
        build_attachment_prompt_blocks(request, {})


def test_gemini_acp_text_attachment_has_legacy_fallback():
    request = AgentRequest(
        execution_id="e3",
        role="architect",
        system_prompt="Inspect.",
        user_prompt="Analyze.",
        attachments=(
            AgentAttachment(
                id=1,
                filename="trace.txt",
                mime_type="text/plain",
                size_bytes=5,
                sha256="e" * 64,
                data=b"trace",
            ),
        ),
    )
    blocks = build_attachment_prompt_blocks(request, {})
    assert blocks[-1]["type"] == "text"
    assert "BEGIN ATTACHMENT" in blocks[-1]["text"]
    assert "trace" in blocks[-1]["text"]
