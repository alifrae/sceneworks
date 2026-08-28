"""WP13: lightweight backlog and deterministic execution-intent routing."""

from __future__ import annotations

import asyncio

from app.agents.fake import FakeAgentBackend, ScriptStep, triage_summary
from app.workflows.adaptive import _resolve_execution_mode


async def _register_project(client, git_repo):
    response = await client.post(
        "/api/projects",
        json={"name": "wp13", "repository_path": str(git_repo)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _wait_task(client, task_id: int, status: str, timeout: float = 10):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] == status:
            return task
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"task never reached {status}: {task['status']}")
        await asyncio.sleep(0.05)


def test_auto_mode_resolution_is_deterministic():
    assert _resolve_execution_mode("change", "product_question", False) == "change"
    assert _resolve_execution_mode("investigate", "feature", True) == "investigate"
    assert _resolve_execution_mode("auto", "feature", True) == "change"
    assert _resolve_execution_mode("auto", "architecture", False) == "plan"
    assert _resolve_execution_mode("auto", "technology_decision", False) == "plan"
    assert _resolve_execution_mode("auto", "product_question", False) == "ask"
    assert _resolve_execution_mode("auto", "technical_investigation", False) == "investigate"


async def test_task_defaults_to_backlog_task_auto(client, git_repo):
    project = await _register_project(client, git_repo)
    response = await client.post(
        "/api/tasks",
        json={"project_id": project["id"], "title": "Remember this for later"},
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "NEW"
    assert task["work_item_type"] == "task"
    assert task["requested_mode"] == "auto"
    assert task["resolved_mode"] is None


async def test_new_backlog_item_can_change_type_mode_and_priority(client, git_repo):
    project = await _register_project(client, git_repo)
    created = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "title": "Playback freezes",
            "work_item_type": "idea",
            "requested_mode": "auto",
        },
    )
    task_id = created.json()["id"]
    response = await client.patch(
        f"/api/tasks/{task_id}",
        json={
            "work_item_type": "bug",
            "requested_mode": "investigate",
            "priority": "high",
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["work_item_type"] == "bug"
    assert task["requested_mode"] == "investigate"
    assert task["resolved_mode"] == "investigate"
    assert task["priority"] == "high"

    filtered = await client.get(
        "/api/tasks",
        params={"work_item_type": "bug", "mode": "investigate", "priority": "high", "query": "freezes"},
    )
    assert [row["id"] for row in filtered.json()] == [task_id]


async def test_explicit_investigate_cannot_be_promoted_to_change(client, context, git_repo):
    project = await _register_project(client, git_repo)
    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "title": "Find the startup regression",
            "requested_mode": "investigate",
        },
    )
    task_id = response.json()["id"]

    backend = FakeAgentBackend(
        role_scripts={
            "triage": [
                ScriptStep(
                    kind="summary",
                    summary=triage_summary(
                        request_type="feature",
                        requires_implementation=True,
                        use_architect=False,
                    ),
                )
            ],
            "architect": [ScriptStep(kind="summary", summary="Read-only root-cause analysis")],
            "engineer": [ScriptStep(kind="file", path="must-not-exist.txt", content="wrong")],
        }
    )
    context.backends._backends["fake"] = backend

    started = await client.post(f"/api/tasks/{task_id}/actions/start-architecture")
    assert started.status_code == 200, started.text
    task = await _wait_task(client, task_id, "READY_FOR_HUMAN")
    assert task["resolved_mode"] == "investigate"
    assert backend.invocations.get("architect", 0) == 1
    assert backend.invocations.get("engineer", 0) == 0

    events = (await client.get(f"/api/tasks/{task_id}/events")).json()
    routing = [event for event in events if event["type"] == "workflow.routing.policy"]
    assert routing
    assert routing[-1]["payload"]["requested_mode"] == "investigate"
    assert routing[-1]["payload"]["resolved_mode"] == "investigate"
    assert routing[-1]["payload"]["mode_source"] == "user"


async def test_explicit_change_cannot_be_downgraded_by_triage(client, context, git_repo):
    project = await _register_project(client, git_repo)
    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "title": "Implement the requested feature",
            "requested_mode": "change",
        },
    )
    task_id = response.json()["id"]

    backend = FakeAgentBackend(
        role_scripts={
            "triage": [
                ScriptStep(
                    kind="summary",
                    summary=triage_summary(
                        request_type="product_question",
                        requires_implementation=False,
                        use_architect=False,
                    ),
                )
            ],
            "architect": [ScriptStep(kind="summary", summary="Implementation plan")],
        }
    )
    context.backends._backends["fake"] = backend

    started = await client.post(f"/api/tasks/{task_id}/actions/start-architecture")
    assert started.status_code == 200, started.text
    task = await _wait_task(client, task_id, "AWAITING_ARCHITECTURE_APPROVAL")
    assert task["resolved_mode"] == "change"

    blocked = await client.patch(
        f"/api/tasks/{task_id}",
        json={"requested_mode": "investigate"},
    )
    assert blocked.status_code == 409
