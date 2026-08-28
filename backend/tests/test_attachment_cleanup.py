"""Attachment lifecycle cleanup tests."""

from __future__ import annotations

import base64

from sqlalchemy import func, select

from app.models import TaskAttachment


async def _setup(client, git_repo, name: str):
    project_response = await client.post(
        "/api/projects",
        json={"name": name, "repository_path": str(git_repo)},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    task_response = await client.post(
        "/api/tasks",
        json={"project_id": project["id"], "title": "context cleanup"},
    )
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    attachment_response = await client.post(
        f"/api/tasks/{task['id']}/attachments",
        json={
            "filename": "context.txt",
            "data_base64": base64.b64encode(b"attachment bytes").decode("ascii"),
        },
    )
    assert attachment_response.status_code == 201, attachment_response.text
    return project, task, attachment_response.json()


async def test_deleting_new_task_removes_attachment_rows_and_files(
    client, context, git_repo
):
    project, task, attachment = await _setup(client, git_repo, "task-cleanup")
    task_dir = context.settings.attachment_root / str(project["id"]) / str(task["id"])
    assert task_dir.exists()

    response = await client.delete(f"/api/tasks/{task['id']}")
    assert response.status_code == 204, response.text
    assert not task_dir.exists()
    async with context.engine_factory() as session:
        assert await session.get(TaskAttachment, attachment["id"]) is None


async def test_project_purge_removes_attachment_rows_and_files_but_not_repository(
    client, context, git_repo
):
    project, task, _ = await _setup(client, git_repo, "project-cleanup")
    project_dir = context.settings.attachment_root / str(project["id"])
    assert project_dir.exists()
    repo_readme = git_repo / "README.md"
    before = repo_readme.read_bytes()

    response = await client.delete(
        f"/api/projects/{project['id']}?purge_history=true&force=true"
    )
    assert response.status_code == 204, response.text
    assert not project_dir.exists()
    async with context.engine_factory() as session:
        count = (
            await session.execute(
                select(func.count(TaskAttachment.id)).where(
                    TaskAttachment.task_id == task["id"]
                )
            )
        ).scalar()
        assert count == 0
    assert git_repo.is_dir()
    assert repo_readme.read_bytes() == before
