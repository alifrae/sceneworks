"""Regression tests for safe project unregister/purge behavior."""

from __future__ import annotations

from app.models import Task


async def _register(client, git_repo):
    response = await client.post(
        "/api/projects",
        json={
            "name": "delete-me",
            "description": "project deletion test",
            "repository_path": str(git_repo),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _task(client, project_id: int):
    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "test task",
            "description": "exercise project cleanup",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_project_unregister_without_history_never_touches_repository(client, git_repo):
    project = await _register(client, git_repo)

    response = await client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 204, response.text
    assert git_repo.is_dir()
    assert (git_repo / ".git").exists()
    assert (git_repo / "README.md").exists()

    response = await client.get(f"/api/projects/{project['id']}")
    assert response.status_code == 404


async def test_project_with_active_work_requires_explicit_force_for_test_cleanup(
    client, context, git_repo
):
    project = await _register(client, git_repo)
    task = await _task(client, project["id"])

    response = await client.delete(
        f"/api/projects/{project['id']}?purge_history=true"
    )
    assert response.status_code == 409
    assert "active" in response.json()["detail"]

    response = await client.delete(
        f"/api/projects/{project['id']}?purge_history=true&force=true"
    )
    assert response.status_code == 204, response.text
    assert git_repo.is_dir()

    response = await client.get(f"/api/projects/{project['id']}")
    assert response.status_code == 404
    response = await client.get(f"/api/tasks/{task['id']}")
    assert response.status_code == 404

    async with context.engine_factory() as session:
        assert await session.get(Task, task["id"]) is None


async def test_terminal_history_can_be_purged_without_force(client, context, git_repo):
    project = await _register(client, git_repo)
    task = await _task(client, project["id"])

    # This test is about deletion semantics, not workflow transitions. Mark the
    # task terminal directly so no agent execution/worktree is involved.
    async with context.engine_factory() as session:
        row = await session.get(Task, task["id"])
        assert row is not None
        row.status = "CANCELLED"
        await session.commit()

    response = await client.delete(
        f"/api/projects/{project['id']}?purge_history=true"
    )
    assert response.status_code == 204, response.text
    assert git_repo.is_dir()

    response = await client.get(f"/api/projects/{project['id']}")
    assert response.status_code == 404
