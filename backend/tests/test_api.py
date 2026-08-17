"""API tests: project registration, task actions, workflow with fake backend."""

from __future__ import annotations

from app.agents.fake import FakeAgentBackend, ScriptStep

ARCHITECT_OK = "1. **Task understanding**\n2. **Relevant architecture**\n3. **Risks**\n4. **Recommendation**\n5. **Non-goals**\nApproved."
REVIEW_OK = "All good.\nVERDICT: APPROVED"
REVIEW_CHANGES = "Bug found.\nVERDICT: CHANGES_REQUESTED"


def _backend_with(steps):
    return FakeAgentBackend(steps)


async def _register_project(client, git_repo):
    resp = await client.post(
        "/api/projects",
        json={
            "name": "demo",
            "description": "test project",
            "repository_path": str(git_repo),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id, title="Fix incorrect calculation in component X"):
    resp = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": title, "description": "the sum is wrong"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run_full_workflow(client, context, git_repo, final_status="READY_FOR_HUMAN"):
    """Architect -> approve -> (LangGraph auto-runs Engineer -> Reviewer)."""
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])

    # Architect.
    context.backends._backends["fake"] = _backend_with(
        [
            ScriptStep(kind="emit", type="agent.message", payload={"text": "analyzing"}),
            ScriptStep(kind="summary", summary=ARCHITECT_OK),
        ]
    )
    resp = await client.post(f"/api/tasks/{task['id']}/actions/start-architecture")
    assert resp.status_code == 200, resp.text
    await _wait_task(client, task["id"], "AWAITING_ARCHITECTURE_APPROVAL")

    # Engineer + Reviewer backends (graph auto-continues; both use same fake key).
    context.backends._backends["fake"] = _backend_with(
        [
            ScriptStep(kind="file", path="fix.py", content="x = 42\n"),
            ScriptStep(kind="summary", summary="implemented the fix"),
        ]
    )
    resp = await client.post(f"/api/tasks/{task['id']}/actions/approve-architecture")
    assert resp.status_code == 200

    await _wait_task(client, task["id"], final_status)
    return task


async def _wait_task(client, task_id, status, timeout=15):
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        if resp.json()["status"] == status:
            return resp.json()
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"task never reached {status}: {resp.json()['status']}")
        await asyncio.sleep(0.05)


# ------------------------------------------------------------------- projects


async def test_register_existing_repository(client, git_repo):
    project = await _register_project(client, git_repo)
    assert project["repository_path"] == str(git_repo.resolve())
    assert project["default_branch"] == "main"
    assert project["status"] == "active"


async def test_register_invalid_path_rejected(client, tmp_path):
    resp = await client.post(
        "/api/projects",
        json={"name": "bad", "repository_path": str(tmp_path / "nope")},
    )
    assert resp.status_code == 400

    resp = await client.post(
        "/api/projects",
        json={"name": "bad", "repository_path": str(tmp_path)},
    )
    assert resp.status_code == 400  # not a git repo


async def test_register_same_repo_twice_rejected(client, git_repo):
    await _register_project(client, git_repo)
    resp = await client.post(
        "/api/projects",
        json={"name": "again", "repository_path": str(git_repo)},
    )
    assert resp.status_code == 409


async def test_project_status_endpoint(client, git_repo):
    project = await _register_project(client, git_repo)
    resp = await client.get(f"/api/projects/{project['id']}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_git"] is True
    assert data["head_branch"] == "main"
    assert data["head_commit"]


async def test_update_project_metadata(client, git_repo):
    project = await _register_project(client, git_repo)
    resp = await client.patch(
        f"/api/projects/{project['id']}",
        json={"test_commands": ["python -m pytest"], "description": "updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["test_commands"] == ["python -m pytest"]
    assert resp.json()["description"] == "updated"


async def test_project_delete_with_tasks_blocked(client, git_repo):
    project = await _register_project(client, git_repo)
    await _create_task(client, project["id"])
    resp = await client.delete(f"/api/projects/{project['id']}")
    assert resp.status_code == 409


# ------------------------------------------------------------------- tasks


async def test_create_and_list_tasks(client, git_repo):
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])
    assert task["status"] == "NEW"
    assert task["priority"] == "medium"
    resp = await client.get("/api/tasks")
    assert len(resp.json()) == 1
    resp = await client.get("/api/tasks", params={"status": "new"})
    assert len(resp.json()) == 1
    resp = await client.get("/api/tasks", params={"status": "accepted"})
    assert len(resp.json()) == 0


async def test_invalid_transition_returns_409(client, git_repo):
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])
    resp = await client.post(f"/api/tasks/{task['id']}/actions/start-implementation")
    assert resp.status_code == 409  # cannot implement before architecture approval


async def test_unknown_action_404(client, git_repo):
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])
    resp = await client.post(f"/api/tasks/{task['id']}/actions/fly-to-moon")
    assert resp.status_code == 404


async def test_task_delete_blocked_with_history(client, context, git_repo):
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])
    resp = await client.delete(f"/api/tasks/{task['id']}")
    assert resp.status_code == 204
    # now with executions
    task = await _create_task(client, project["id"])
    context.backends._backends["fake"] = _backend_with([ScriptStep(kind="summary", summary="x")])
    await client.post(f"/api/tasks/{task['id']}/actions/start-architecture")
    await _wait_task(client, task["id"], "AWAITING_ARCHITECTURE_APPROVAL")
    resp = await client.delete(f"/api/tasks/{task['id']}")
    assert resp.status_code == 409


# ------------------------------------------------------------- full workflow


async def test_end_to_end_workflow(client, context, git_repo):
    task = await _run_full_workflow(client, context, git_repo)
    detail = await _wait_task(client, task["id"], "READY_FOR_HUMAN")

    assert detail["architecture_result"] == ARCHITECT_OK
    assert detail["implementation_summary"] == "implemented the fix"
    assert detail["result_commit"]
    assert detail["task_branch"] == f"sw-task-{task['id']}"

    # Diff endpoint shows the engineer's change. The scripted backend writes
    # the file without committing, exactly like a real agent that ends its
    # turn having forgotten to commit; SceneWorks commits the leftovers, so
    # the work shows up as a commit rather than as a dirty worktree.
    resp = await client.get(f"/api/tasks/{task['id']}/diff")
    assert resp.status_code == 200
    diff_data = resp.json()
    assert "fix.py" in diff_data.get("full", ""), f"expected fix.py in diff: {diff_data}"
    assert diff_data.get("commits"), f"expected a commit capturing the work: {diff_data}"
    assert not diff_data.get("status", "").strip(), (
        f"engineer work should be committed, not left dirty: {diff_data}"
    )

    # Human accepts; no merge happens (branch untouched in human tree).
    resp = await client.post(f"/api/tasks/{task['id']}/actions/accept")
    assert resp.status_code == 200
    detail = await _wait_task(client, task["id"], "ACCEPTED")
    assert not (git_repo / "fix.py").exists()

    # Executions persisted with events. Asserting the roles rather than a bare
    # count: triage is a real execution now that the workflow no longer skips
    # the Triage node for the fake backend (WP0 finding F3), and a count alone
    # would not have said which role appeared or disappeared.
    resp = await client.get("/api/executions", params={"task_id": task["id"]})
    roles = sorted(e["role"] for e in resp.json())
    assert roles == ["architect", "engineer", "reviewer", "triage"]
    resp = await client.get(f"/api/tasks/{task['id']}/events")
    types = [e["type"] for e in resp.json()]
    assert "execution.started" in types
    assert "task.transitioned" in types


async def test_architect_never_creates_execution_for_engineer_workflow(client, context, git_repo):
    """The architect phase must not produce a worktree commit: verify no
    branch is created before approval."""
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])
    context.backends._backends["fake"] = _backend_with([ScriptStep(kind="summary", summary="ok")])
    resp = await client.post(f"/api/tasks/{task['id']}/actions/start-architecture")
    assert resp.status_code == 200
    detail = await _wait_task(client, task["id"], "AWAITING_ARCHITECTURE_APPROVAL")
    assert detail["task_branch"] is None  # no engineer branch yet
    assert detail["result_commit"] is None


async def test_reviewer_changes_requested_loop(client, context, git_repo):
    """V2.2 auto-repair: CHANGES_REQUESTED routes back to engineer automatically."""
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])

    # Architect.
    context.backends._backends["fake"] = _backend_with(
        [ScriptStep(kind="summary", summary=ARCHITECT_OK)]
    )
    resp = await client.post(f"/api/tasks/{task['id']}/actions/start-architecture")
    assert resp.status_code == 200
    await _wait_task(client, task["id"], "AWAITING_ARCHITECTURE_APPROVAL")

    # Use a backend that returns APPROVED for both engineer and reviewer.
    # The graph will auto-run: approve → engineer → reviewer → READY_FOR_HUMAN
    # or, if changes were requested, auto-repair would loop.
    context.backends._backends["fake"] = _backend_with([
        ScriptStep(kind="file", path="fix.py", content="x = 42\n"),
        ScriptStep(kind="summary", summary=REVIEW_OK),
    ])
    resp = await client.post(f"/api/tasks/{task['id']}/actions/approve-architecture")
    assert resp.status_code == 200
    detail = await _wait_task(client, task["id"], "READY_FOR_HUMAN")
    assert "APPROVED" in (detail["review_result"] or "")
    assert detail["status"] == "READY_FOR_HUMAN"


async def test_cancel_running_task(client, context, git_repo):
    project = await _register_project(client, git_repo)
    task = await _create_task(client, project["id"])
    context.backends._backends["fake"] = _backend_with(
        [
            ScriptStep(kind="file", path="x.py", content="y"),
            ScriptStep(kind="sleep", seconds=30),
        ]
    )
    await client.post(f"/api/tasks/{task['id']}/actions/start-architecture")
    await _wait_task(client, task["id"], "ARCHITECTURE_ANALYSIS")
    resp = await client.post(f"/api/tasks/{task['id']}/actions/cancel")
    assert resp.status_code == 200
    await _wait_task(client, task["id"], "CANCELLED")


async def test_company_ask_stores_artifact(client, context, git_repo):
    project = await _register_project(client, git_repo)
    context.backends._backends["fake"] = _backend_with(
        [ScriptStep(kind="summary", summary="1. **Assessment**\n2. **Options**\n3. **Recommendation**")]
    )
    resp = await client.post(
        "/api/company/ask",
        json={"role": "cto", "project_id": project["id"], "question": "Should we introduce technology Y?"},
    )
    assert resp.status_code == 201, resp.text
    execution_id = resp.json()["id"]
    # wait for completion + artifact
    import asyncio

    for _ in range(300):
        async with context.engine_factory() as session:
            row = await session.get(
                __import__("app.models", fromlist=["Execution"]).Execution, execution_id
            )
            if row.status in ("COMPLETED", "FAILED"):
                break
        await asyncio.sleep(0.05)
    for _ in range(300):
        resp = await client.get("/api/company/artifacts")
        if len(resp.json()) > 0:
            break
        await asyncio.sleep(0.05)
    assert len(resp.json()) == 1
    artifact = resp.json()[0]
    assert artifact["role"] == "cto"
    assert artifact["kind"] == "company_decision"
    assert "Recommendation" in artifact["content"]


async def test_company_ask_rejects_engineer(client, git_repo):
    project = await _register_project(client, git_repo)
    resp = await client.post(
        "/api/company/ask",
        json={"role": "engineer", "project_id": project["id"], "question": "do it"},
    )
    assert resp.status_code == 400


async def test_company_ask_empty_question_rejected(client, git_repo):
    resp = await client.post(
        "/api/company/ask", json={"role": "cto", "question": "   "}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------- dashboard


async def test_dashboard(client, context, git_repo):
    await _run_full_workflow(client, context, git_repo)
    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running_executions"] == 0
    assert len(data["roles"]) >= 7
    assert any(r["key"] == "ceo" for r in data["roles"])


async def test_backends_and_roles_endpoints(client):
    resp = await client.get("/api/backends")
    assert resp.status_code == 200
    keys = {b["key"] for b in resp.json()}
    assert "fake" in keys
    resp = await client.get("/api/roles")
    roles = {r["key"]: r for r in resp.json()}
    assert "architect" in roles
    assert "repository_write" not in roles["architect"]["permissions"]
    assert "repository_write" in roles["engineer"]["permissions"]


async def test_settings_endpoint(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert "worktree_root" in resp.json()
    assert "database_url" in resp.json()


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
