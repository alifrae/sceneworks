"""WP11 adaptive-role routing regression tests."""

from __future__ import annotations

import asyncio

from app.agents.fake import FakeAgentBackend, ScriptStep, triage_summary
from app.workflows.adaptive import _bounded_contract

REVIEW_OK = "Independent review passed.\nVERDICT: APPROVED"


async def _register_project(client, git_repo):
    response = await client.post(
        "/api/projects",
        json={"name": "routing-demo", "repository_path": str(git_repo)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_task(client, project_id, *, bounded: bool):
    contract = {}
    if bounded:
        contract = {
            "allowed_scope": ["app.py"],
            "required_tests": ["pytest -q"],
            "acceptance_criteria": ["calculation is correct"],
        }
    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Fix calculation regression",
            "description": "A local bug in app.py returns the wrong calculation.",
            "priority": "low",
            "engineering_contract": contract,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _wait_status(client, task_id, expected, timeout=15):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] == expected:
            return task
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(f"task never reached {expected}: {task['status']}")
        await asyncio.sleep(0.05)


def test_bounded_contract_gate_requires_scope_tests_and_acceptance():
    assert _bounded_contract(
        {
            "allowed_scope": ["app.py"],
            "required_tests": ["pytest"],
            "acceptance_criteria": ["fixed"],
        }
    )
    assert not _bounded_contract({"allowed_scope": ["app.py"], "required_tests": ["pytest"]})
    assert not _bounded_contract(
        {"allowed_scope": [], "required_tests": ["pytest"], "acceptance_criteria": ["fixed"]}
    )


async def test_bounded_low_risk_bug_skips_architect_but_keeps_reviewer(
    client, context, git_repo
):
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"], bounded=True)

    backend = FakeAgentBackend(
        role_scripts={
            "triage": [
                ScriptStep(
                    kind="summary",
                    summary=triage_summary(
                        request_type="bug",
                        use_architect=True,
                        use_product=False,
                        use_cto=False,
                        use_technical_expert=False,
                        requires_implementation=True,
                    ),
                )
            ],
            "engineer": [
                ScriptStep(kind="file", path="app.py", content="def main():\n    return 2\n"),
                ScriptStep(kind="summary", summary="fixed bounded calculation"),
            ],
            "reviewer": [ScriptStep(kind="summary", summary=REVIEW_OK)],
            "architect": [ScriptStep(kind="summary", summary="SHOULD NOT RUN")],
        }
    )
    context.backends._backends["fake"] = backend

    response = await client.post(f"/api/tasks/{task['id']}/actions/start-architecture")
    assert response.status_code == 200, response.text
    final = await _wait_status(client, task["id"], "READY_FOR_HUMAN")

    assert backend.invocations.get("triage") == 1
    assert backend.invocations.get("architect", 0) == 0
    assert backend.invocations.get("engineer") == 1
    assert backend.invocations.get("reviewer") == 1
    assert final["result_commit"]

    events = await client.get(f"/api/tasks/{task['id']}/events")
    assert events.status_code == 200
    routing = [e for e in events.json() if e["type"] == "workflow.routing.policy"]
    assert routing
    assert routing[-1]["payload"]["decision"] == "direct_implementation"
    transitions = [e for e in events.json() if e["type"] == "task.transitioned"]
    assert any(e["payload"].get("action") == "skip_architecture" for e in transitions)


async def test_unbounded_bug_cannot_skip_architecture_even_if_triage_requests_it(
    client, context, git_repo
):
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"], bounded=False)
    backend = FakeAgentBackend(
        role_scripts={
            "triage": [
                ScriptStep(
                    kind="summary",
                    summary=triage_summary(
                        request_type="bug",
                        use_architect=False,
                        requires_implementation=True,
                    ),
                )
            ],
            "architect": [ScriptStep(kind="summary", summary="architecture required")],
        }
    )
    context.backends._backends["fake"] = backend

    response = await client.post(f"/api/tasks/{task['id']}/actions/start-architecture")
    assert response.status_code == 200, response.text
    await _wait_status(client, task["id"], "AWAITING_ARCHITECTURE_APPROVAL")

    assert backend.invocations.get("triage") == 1
    assert backend.invocations.get("architect") == 1
    assert backend.invocations.get("engineer", 0) == 0
