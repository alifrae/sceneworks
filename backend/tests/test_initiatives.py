"""WP5 Project -> Initiative -> WorkPackage -> Task hierarchy tests."""

from __future__ import annotations


async def _project(client, git_repo, name="initiative-project") -> dict:
    response = await client.post(
        "/api/projects", json={"name": name, "repository_path": str(git_repo)}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_initiative_work_package_and_task_hierarchy(client, git_repo):
    project = await _project(client, git_repo)

    response = await client.post(
        f"/api/projects/{project['id']}/initiatives",
        json={
            "title": "Multi-file recordings",
            "objective": "Open and coordinate recordings from multiple sensors",
        },
    )
    assert response.status_code == 201, response.text
    initiative = response.json()
    assert initiative["status"] == "planned"
    assert initiative["work_package_count"] == 0

    response = await client.post(
        f"/api/initiatives/{initiative['id']}/work-packages",
        json={
            "key": "WP1",
            "title": "Session model",
            "acceptance_criteria": ["Multiple sources can coexist in one session"],
        },
    )
    assert response.status_code == 201, response.text
    wp1 = response.json()
    assert wp1["sequence"] == 1

    response = await client.post(
        f"/api/initiatives/{initiative['id']}/work-packages",
        json={
            "key": "WP2",
            "title": "Loader integration",
            "depends_on": [wp1["id"]],
        },
    )
    assert response.status_code == 201, response.text
    wp2 = response.json()
    assert wp2["sequence"] == 2
    assert wp2["depends_on"] == [wp1["id"]]

    response = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "work_package_id": wp2["id"],
            "title": "Implement loader integration",
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["work_package_id"] == wp2["id"]

    response = await client.get(f"/api/initiatives/{initiative['id']}")
    assert response.status_code == 200
    summary = response.json()
    assert summary["work_package_count"] == 2
    assert summary["task_count"] == 1

    response = await client.get(
        f"/api/initiatives/{initiative['id']}/work-packages"
    )
    assert [row["key"] for row in response.json()] == ["WP1", "WP2"]
    assert response.json()[1]["task_count"] == 1


async def test_work_package_dependencies_are_same_initiative_and_acyclic(client, git_repo):
    project = await _project(client, git_repo)
    first = (
        await client.post(
            f"/api/projects/{project['id']}/initiatives", json={"title": "First"}
        )
    ).json()
    second = (
        await client.post(
            f"/api/projects/{project['id']}/initiatives", json={"title": "Second"}
        )
    ).json()

    a = (
        await client.post(
            f"/api/initiatives/{first['id']}/work-packages",
            json={"key": "A", "title": "A"},
        )
    ).json()
    b = (
        await client.post(
            f"/api/initiatives/{first['id']}/work-packages",
            json={"key": "B", "title": "B", "depends_on": [a["id"]]},
        )
    ).json()
    foreign = (
        await client.post(
            f"/api/initiatives/{second['id']}/work-packages",
            json={"key": "X", "title": "X"},
        )
    ).json()

    response = await client.patch(
        f"/api/work-packages/{a['id']}", json={"depends_on": [b["id"]]}
    )
    assert response.status_code == 400
    assert "acyclic" in response.text

    response = await client.patch(
        f"/api/work-packages/{a['id']}", json={"depends_on": [foreign["id"]]}
    )
    assert response.status_code == 400
    assert "same initiative" in response.text


async def test_task_cannot_attach_to_work_package_from_another_project(
    client, git_repo, tmp_path
):
    first = await _project(client, git_repo, "first")

    # A second repository is required because project registration correctly
    # rejects duplicate repository paths.
    from tests.conftest import git

    second_repo = tmp_path / "second-repo"
    second_repo.mkdir()
    git(second_repo, "init", "-b", "main")
    git(second_repo, "config", "user.email", "test@sceneworks.local")
    git(second_repo, "config", "user.name", "SceneWorks Test")
    (second_repo / "README.md").write_text("# second\n", encoding="utf-8")
    git(second_repo, "add", "-A")
    git(second_repo, "commit", "-m", "initial")
    second = await _project(client, second_repo, "second")

    initiative = (
        await client.post(
            f"/api/projects/{first['id']}/initiatives", json={"title": "Owned by first"}
        )
    ).json()
    wp = (
        await client.post(
            f"/api/initiatives/{initiative['id']}/work-packages",
            json={"key": "WP1", "title": "First-only package"},
        )
    ).json()

    response = await client.post(
        "/api/tasks",
        json={
            "project_id": second["id"],
            "work_package_id": wp["id"],
            "title": "invalid cross-project task",
        },
    )
    assert response.status_code == 400


async def test_initiative_cannot_complete_with_unfinished_work_packages(client, git_repo):
    project = await _project(client, git_repo)
    initiative = (
        await client.post(
            f"/api/projects/{project['id']}/initiatives", json={"title": "Release"}
        )
    ).json()
    wp = (
        await client.post(
            f"/api/initiatives/{initiative['id']}/work-packages",
            json={"key": "WP1", "title": "Required work"},
        )
    ).json()

    response = await client.patch(
        f"/api/initiatives/{initiative['id']}", json={"status": "completed"}
    )
    assert response.status_code == 409

    response = await client.patch(
        f"/api/work-packages/{wp['id']}", json={"status": "completed"}
    )
    assert response.status_code == 200

    response = await client.patch(
        f"/api/initiatives/{initiative['id']}", json={"status": "completed"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
