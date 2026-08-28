"""WP15 evidence-domain invariants independent of provider execution."""

from __future__ import annotations

import subprocess

import pytest

from app.services.engineering_evidence import EngineeringEvidenceError
from app.services.engineering_sessions import EngineeringSessionError


def _git(repo, *args: str) -> None:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


async def _project(client, path, name: str):
    response = await client.post(
        "/api/projects",
        json={"name": name, "repository_path": str(path)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_cross_project_task_cannot_bind_engineering_session(
    client, context, git_repo, tmp_path
):
    other = tmp_path / "other-repo"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _git(other, "config", "user.email", "test@sceneworks.local")
    _git(other, "config", "user.name", "SceneWorks Test")
    (other / "README.md").write_text("# other\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "initial")

    first = await _project(client, git_repo, "first")
    second = await _project(client, other, "second")
    task_response = await client.post(
        "/api/tasks",
        json={"project_id": second["id"], "title": "other task"},
    )
    assert task_response.status_code == 201
    task = task_response.json()

    with pytest.raises(EngineeringSessionError, match="belongs to project"):
        await context.engineering_sessions.create(
            first["id"],
            task_id=task["id"],
            permissions=["repository_read"],
        )


async def test_only_one_active_turn_per_engineering_session(
    client, context, git_repo
):
    project = await _project(client, git_repo, "turns")
    engineering = await context.engineering_sessions.create(
        project["id"], permissions=["repository_read"]
    )
    first = await context.engineering_evidence.begin_turn(
        engineering.id, "first iteration"
    )

    with pytest.raises(EngineeringEvidenceError, match="already has active turn"):
        await context.engineering_evidence.begin_turn(
            engineering.id, "overlapping iteration"
        )

    await context.engineering_evidence.finish_turn(
        engineering.id, first.id, "COMPLETED"
    )
    second = await context.engineering_evidence.begin_turn(
        engineering.id, "next iteration"
    )
    assert second.id != first.id
