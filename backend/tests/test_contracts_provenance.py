"""WP4 engineering-contract and WP6 provenance integration tests."""

from __future__ import annotations

from app.models import Task


async def _project(client, git_repo) -> dict:
    response = await client.post(
        "/api/projects",
        json={"name": "contract-test", "repository_path": str(git_repo)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_engineering_contract_round_trips_and_is_immutable_after_start(
    client, context, git_repo
):
    project = await _project(client, git_repo)
    contract = {
        "required_behavior": ["preserve existing callers"],
        "allowed_scope": ["app.py"],
        "forbidden_changes": ["do not edit README.md"],
        "architecture_constraints": ["keep the public function name"],
        "required_tests": ["python -m pytest"],
        "performance_requirements": ["no additional file IO"],
        "compatibility_requirements": ["Python 3.12"],
        "acceptance_criteria": ["main() still returns an integer"],
    }
    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "title": "contracted change",
            "description": "make a bounded implementation change",
            "engineering_contract": contract,
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["engineering_contract"] == contract
    assert task["changed_files"] == []

    replacement = {**contract, "acceptance_criteria": ["updated criterion"]}
    response = await client.put(
        f"/api/tasks/{task['id']}/contract", json=replacement
    )
    assert response.status_code == 200, response.text
    assert response.json()["engineering_contract"] == replacement

    async with context.engine_factory() as session:
        row = await session.get(Task, task["id"])
        row.status = "ARCHITECTURE_ANALYSIS"
        await session.commit()

    response = await client.put(
        f"/api/tasks/{task['id']}/contract", json=contract
    )
    assert response.status_code == 409


async def test_contract_is_rendered_identically_and_reviewer_must_verify_it(
    client, context, git_repo
):
    project = await _project(client, git_repo)
    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "title": "review contract",
            "engineering_contract": {
                "allowed_scope": ["app.py"],
                "required_tests": ["python check.py"],
                "acceptance_criteria": ["return value is 2"],
            },
        },
    )
    task_id = response.json()["id"]

    async with context.engine_factory() as session:
        task = await session.get(Task, task_id)
        project_row = task.project
        # Relationship may not be eagerly loaded in async mode; fetch directly.
        if project_row is None:
            from app.models import Project
            project_row = await session.get(Project, task.project_id)

    workspace = {
        "cwd": str(git_repo),
        "repo_path": str(git_repo),
        "branch": "main",
        "base_commit": await context.git.head_commit(git_repo),
        "permissions": ["repository_read"],
    }
    architect_prompt = await context.prompt_builder.build(
        role=context.roles.effective("architect"),
        project=project_row,
        task=task,
        workspace=workspace,
        context_worktree_path=str(git_repo),
    )
    reviewer_prompt = await context.prompt_builder.build(
        role=context.roles.effective("reviewer"),
        project=project_row,
        task=task,
        workspace=workspace,
        context_worktree_path=str(git_repo),
    )

    for prompt in (architect_prompt.user, reviewer_prompt.user):
        assert "# Engineering contract (binding)" in prompt
        assert "- app.py" in prompt
        assert "- python check.py" in prompt
        assert "- return value is 2" in prompt
    assert "If a required criterion or test cannot be verified" in reviewer_prompt.user


async def test_git_provenance_is_persisted_and_queryable_by_path(
    client, context, git_repo
):
    project = await _project(client, git_repo)
    response = await client.post(
        "/api/tasks",
        json={"project_id": project["id"], "title": "change app.py"},
    )
    assert response.status_code == 201
    task_id = response.json()["id"]

    base = await context.git.head_commit(git_repo)
    (git_repo / "app.py").write_text(
        "def main():\n    return 3\n", encoding="utf-8"
    )
    result = await context.git.commit_all(git_repo, "change app implementation")

    async with context.engine_factory() as session:
        task = await session.get(Task, task_id)
        task.base_commit = base
        task.result_commit = result
        task.worktree_path = str(git_repo)
        task.task_branch = "sw-task-test"
        task.status = "READY_FOR_HUMAN"
        await session.commit()

    response = await client.get(f"/api/tasks/{task_id}/provenance")
    assert response.status_code == 200, response.text
    provenance = response.json()
    assert provenance["base_commit"] == base
    assert provenance["result_commit"] == result
    assert provenance["changed_files"] == ["app.py"]

    # The path list is persisted, not just returned from a one-off Git query.
    async with context.engine_factory() as session:
        task = await session.get(Task, task_id)
        assert task.changed_files == ["app.py"]

    response = await client.get(
        f"/api/projects/{project['id']}/provenance", params={"path": "app.py"}
    )
    assert response.status_code == 200, response.text
    rows = response.json()["tasks"]
    assert [row["task_id"] for row in rows] == [task_id]

    response = await client.get(
        f"/api/projects/{project['id']}/provenance", params={"path": "README.md"}
    )
    assert response.status_code == 200
    assert response.json()["tasks"] == []
