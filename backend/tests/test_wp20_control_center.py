import pytest

from app.engineering_models import EngineeringEvidence, EngineeringSession
from app.models import Task
from app.pcs_models import PcsRun


@pytest.mark.asyncio
async def test_wp20_control_center_is_bounded_operational_snapshot(client, context, git_repo):
    created = await client.post(
        "/api/projects",
        json={
            "name": "Control project",
            "description": "WP20",
            "repository_path": str(git_repo),
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    task_response = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Playback regression",
            "description": "Playback pauses unexpectedly",
            "work_item_type": "bug",
            "priority": "high",
            "requested_mode": "investigate",
        },
    )
    assert task_response.status_code == 201, task_response.text
    task_id = task_response.json()["id"]

    async with context.engine_factory() as db:
        engineering = EngineeringSession(
            project_id=project_id,
            task_id=task_id,
            runtime="native",
            status="ACTIVE",
            base_commit="a" * 40,
            branch="sw/mcp-test",
            permissions=["repository_read", "process_control"],
        )
        db.add(engineering)
        await db.flush()
        run = PcsRun(
            project_id=project_id,
            engineering_session_id=engineering.id,
            task_id=task_id,
            profile_name="debug",
            process_id="wp20-test-process",
            pid=4321,
            status="RUNNING",
        )
        db.add(run)
        db.add(
            EngineeringEvidence(
                engineering_session_id=engineering.id,
                task_id=task_id,
                action_id="wp20-action-1",
                category="pcs",
                operation="pcs.health",
                status="SUCCESS",
                payload={"private": "must not leak through control-center aggregate"},
            )
        )
        await db.commit()

    response = await client.get("/api/control-center")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["projects"] == 1
    assert body["issues"]["open"] == 1
    assert body["engineering_sessions"][0]["status"] == "ACTIVE"
    assert body["engineering_sessions"][0]["task_title"] == "Playback regression"
    assert "worktree_path" not in body["engineering_sessions"][0]
    assert body["pcs_runs"][0]["profile"] == "debug"
    assert body["pcs_runs"][0]["pid"] == 4321
    assert body["recent_evidence"][0]["operation"] == "pcs.health"
    assert "payload" not in body["recent_evidence"][0]


@pytest.mark.asyncio
async def test_wp20_force_unregister_removes_stale_backlog_history(client, git_repo):
    created = await client.post(
        "/api/projects",
        json={"name": "Disposable", "repository_path": str(git_repo)},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    task_response = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Stale issue",
            "description": "generated test data",
            "work_item_type": "bug",
        },
    )
    assert task_response.status_code == 201, task_response.text

    deleted = await client.delete(
        f"/api/projects/{project_id}?purge_history=true&force=true"
    )
    assert deleted.status_code == 204, deleted.text
    assert (await client.get(f"/api/projects/{project_id}")).status_code == 404
