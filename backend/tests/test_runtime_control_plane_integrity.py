"""Regression coverage for the Windows runtime/control-plane integrity repair."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.agents.integrity import IntegrityBackendRegistry
from app.models import Artifact, Execution, ProjectMemory, Task
from app.workflows.integrity import IntegrityWorkflowRecovery


async def _register_project(client, git_repo: Path, **overrides) -> dict:
    payload = {
        "name": "integrity-project",
        "description": "control-plane integrity regression",
        "repository_path": str(git_repo),
        "architecture_context_paths": ["docs/architecture.md"],
        "test_commands": ["pytest -q"],
        "build_commands": ["python -m build"],
        "capability_profile": {"skills": ["repository"]},
        **overrides,
    }
    response = await client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _rpc(client, name: str, arguments: dict, request_id: int = 1) -> dict:
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
    result = response.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_windows_launcher_syncs_pinned_openhands_extra() -> None:
    launcher = Path(__file__).resolve().parents[2] / "scripts" / "start-sceneworks.ps1"
    text = launcher.read_text(encoding="utf-8")
    assert "uv sync --frozen --extra openhands" in text


class _EmptyFailureBackend:
    key = "broken"
    label = "Broken probe"

    async def health(self):
        raise NotImplementedError()

    async def run(self, request, workspace, event_sink):  # pragma: no cover - health only
        raise AssertionError("not used")

    async def cancel(self, execution_id):  # pragma: no cover - health only
        return None


async def test_backend_health_preserves_empty_exception_type(settings) -> None:
    registry = IntegrityBackendRegistry(
        settings, include_fake=False, include_openhands=False
    )
    registry._backends = {"broken": _EmptyFailureBackend()}

    health = await registry.health_all(force=True)

    assert len(health) == 1
    assert health[0].available is False
    assert health[0].detail == "health check failed: NotImplementedError"
    assert len(health[0].detail) <= 400
    await registry.shutdown()


async def test_git_snapshot_reports_current_truth(context, git_repo: Path) -> None:
    snapshot = await context.git.repository_snapshot(git_repo, "main")
    assert snapshot["is_git"] is True
    assert snapshot["current_branch"] == "main"
    assert snapshot["default_branch"] == "main"
    assert snapshot["head_commit"] == _git(git_repo, "rev-parse", "HEAD")
    assert snapshot["clean"] is True
    assert snapshot["dirty"] is False
    assert snapshot["availability"]["state"] == "available"
    assert snapshot["diagnostic"] is None

    (git_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    dirty = await context.git.repository_snapshot(git_repo, "main")
    assert dirty["dirty"] is True
    assert dirty["clean"] is False


async def test_git_probe_failure_is_typed(context, git_repo: Path, monkeypatch) -> None:
    async def fail_probe(*args, **kwargs):
        raise NotImplementedError()

    monkeypatch.setattr(context.git, "_run", fail_probe)
    info = await context.git.repo_info(git_repo)

    assert info.is_git is False
    assert info.error is not None
    assert "git_probe_failed" in info.error
    assert "NotImplementedError" in info.error


async def test_project_context_exposes_persisted_config_and_typed_snapshot(
    client, context, git_repo: Path
) -> None:
    project = await _register_project(client, git_repo)
    policy = {
        "protected_paths": ["backend/app/models.py"],
        "required_review_checks": ["pytest -q"],
    }
    response = await client.put(f"/api/projects/{project['id']}/policy", json=policy)
    assert response.status_code == 200, response.text

    async with context.engine_factory() as session:
        session.add(
            ProjectMemory(
                project_id=project["id"],
                type="fact",
                title="accepted truth",
                content="accepted memory content",
                status="accepted",
                source="test",
            )
        )
        session.add(
            ProjectMemory(
                project_id=project["id"],
                type="hypothesis",
                title="proposal",
                content="must not be authoritative",
                status="proposed",
                source="test",
            )
        )
        await session.commit()

    result = await _rpc(
        client,
        "sceneworks.get_project_context",
        {"project_id": project["id"]},
    )

    persisted = result["project"]
    assert persisted["architecture_context_paths"] == ["docs/architecture.md"]
    assert persisted["test_commands"] == ["pytest -q"]
    assert persisted["build_commands"] == ["python -m build"]
    assert persisted["engineering_policy"]["protected_paths"] == ["backend/app/models.py"]
    assert persisted["capability_profile"]["skills"] == ["repository"]
    assert [row["title"] for row in result["accepted_memory"]] == ["accepted truth"]

    snapshot = result["repository_snapshot"]
    assert snapshot["is_git"] is True
    assert snapshot["current_branch"] == "main"
    assert snapshot["default_branch"] == "main"
    assert snapshot["head_commit"]
    assert snapshot["clean"] is True
    assert snapshot["diagnostic"] is None
    assert "repository_path" not in json.dumps(result)


async def test_project_context_repository_probe_failure_is_data_not_server_error(
    client, context, git_repo: Path, monkeypatch
) -> None:
    project = await _register_project(client, git_repo)

    async def fail_probe(*args, **kwargs):
        raise NotImplementedError()

    monkeypatch.setattr(context.git, "_run", fail_probe)
    result = await _rpc(
        client,
        "sceneworks.get_project_context",
        {"project_id": project["id"]},
    )

    snapshot = result["repository_snapshot"]
    assert snapshot["is_git"] is False
    assert snapshot["availability"]["state"] == "unavailable"
    assert snapshot["diagnostic"]["code"] == "git_probe_failed"
    assert snapshot["diagnostic"]["exception_type"] == "NotImplementedError"


async def test_pcs_config_distinguishes_unconfigured_from_configured_empty(
    client, git_repo: Path
) -> None:
    project = await _register_project(client, git_repo)

    missing = await _rpc(
        client,
        "sceneworks.pcs.get_config",
        {"project_id": project["id"]},
    )
    assert missing["availability"]["state"] == "not_configured"
    assert missing["config"] is None

    response = await client.put(
        f"/api/projects/{project['id']}/pcs-control",
        json={"profiles": {}, "runbooks": {}, "asset_roots": {}},
    )
    assert response.status_code == 200, response.text

    configured = await _rpc(
        client,
        "sceneworks.pcs.get_config",
        {"project_id": project["id"]},
        request_id=2,
    )
    assert configured["availability"]["state"] == "available"
    assert configured["config"]["profiles"] == {}
    assert configured["config"]["runbooks"] == {}
    assert configured["config"]["asset_roots"] == {}


class _RecoveryOwner:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self.events: list[tuple[int, str, dict]] = []

    def _config(self, task_id: int) -> dict:
        return {"configurable": {"thread_id": f"task-{task_id}"}}

    async def _ensure_checkpointer(self):
        return self

    async def aget(self, config):
        return {"checkpoint": "present"}

    async def _emit_workflow_event(self, task_id: int, event_type: str, payload: dict):
        self.events.append((task_id, event_type, payload))

    async def start_implementation(self, task_id: int):  # pragma: no cover - not this state
        raise AssertionError("approval wait must not auto-resume")


async def test_rehydrating_approval_wait_is_idempotent(context, git_repo: Path) -> None:
    project = await _register_project_for_db(context, git_repo)
    async with context.engine_factory() as session:
        task = Task(
            project_id=project.id,
            title="approval wait",
            description="",
            status="AWAITING_ARCHITECTURE_APPROVAL",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

    owner = _RecoveryOwner(context.engine_factory)
    recovery = IntegrityWorkflowRecovery(owner)
    await recovery.recover()
    await recovery.recover()

    assert owner.events == []


async def _register_project_for_db(context, git_repo: Path):
    from app.models import Project

    async with context.engine_factory() as session:
        project = Project(
            name=f"db-project-{uuid.uuid4().hex[:8]}",
            repository_path=str(git_repo.resolve()),
            default_branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def test_task_diff_survives_worktree_cleanup_via_immutable_commits(
    client, context, git_repo: Path
) -> None:
    project = await _register_project(client, git_repo)
    base = _git(git_repo, "rev-parse", "HEAD")
    (git_repo / "app.py").write_text("def main():\n    return 42\n", encoding="utf-8")
    _git(git_repo, "add", "app.py")
    _git(git_repo, "commit", "-m", "change result")
    result_commit = _git(git_repo, "rev-parse", "HEAD")

    async with context.engine_factory() as session:
        task = Task(
            project_id=project["id"],
            title="completed task",
            description="",
            status="ACCEPTED",
            base_commit=base,
            result_commit=result_commit,
            worktree_path=None,
            changed_files=["app.py"],
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    diff = await _rpc(
        client,
        "sceneworks.get_task_diff",
        {"task_id": task_id},
    )
    assert diff["available"] is True
    assert diff["source"] == "immutable_commits"
    assert diff["base_commit"] == base
    assert diff["result_commit"] == result_commit
    assert "return 42" in diff["diff"]
    assert "app.py" in diff["changed_files"]

    task_view = await _rpc(
        client,
        "sceneworks.get_task",
        {"task_id": task_id},
        request_id=2,
    )
    assert task_view["task"]["status"] == "ACCEPTED"


async def test_missing_result_diff_is_typed_unavailable_not_tool_error(
    client, context, git_repo: Path
) -> None:
    project = await _register_project(client, git_repo)
    base = _git(git_repo, "rev-parse", "HEAD")
    async with context.engine_factory() as session:
        task = Task(
            project_id=project["id"],
            title="cancelled task",
            description="",
            status="CANCELLED",
            base_commit=base,
            result_commit=None,
            worktree_path=None,
            changed_files=[],
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    diff = await _rpc(
        client,
        "sceneworks.get_task_diff",
        {"task_id": task_id},
    )
    assert diff["available"] is False
    assert diff["availability"]["state"] == "unavailable"
    assert diff["availability"]["reason"] == "result_commit_missing"

    task_view = await _rpc(
        client,
        "sceneworks.get_task",
        {"task_id": task_id},
        request_id=2,
    )
    assert task_view["task"]["status"] == "CANCELLED"


async def test_company_artifact_persistence_is_exactly_once_and_listable(
    client, context, git_repo: Path
) -> None:
    project = await _register_project(client, git_repo)
    execution_id = uuid.uuid4().hex
    async with context.engine_factory() as session:
        session.add(
            Execution(
                id=execution_id,
                task_id=None,
                role="cto",
                backend="fake",
                status="COMPLETED",
                result="immutable role output",
                workspace={
                    "project_id": project["id"],
                    "ask_title": "architecture decision",
                    "base_commit": _git(git_repo, "rev-parse", "HEAD"),
                },
            )
        )
        await session.commit()

    await context.workflow_manager.on_execution_finished(execution_id)
    await context.workflow_manager.on_execution_finished(execution_id)

    async with context.engine_factory() as session:
        count = (
            await session.execute(
                select(func.count(Artifact.id)).where(
                    Artifact.source_execution_id == execution_id
                )
            )
        ).scalar_one()
    assert count == 1

    artifacts = await _rpc(
        client,
        "sceneworks.list_artifacts",
        {"project_id": project["id"]},
    )
    matching = [
        row for row in artifacts["artifacts"]
        if row["source_execution_id"] == execution_id
    ]
    assert len(matching) == 1
    assert "immutable role output" in matching[0]["content"]


async def test_fake_backend_does_not_seed_operator_visible_tasks(context) -> None:
    assert "fake" in context.backends.keys()
    async with context.engine_factory() as session:
        count = (await session.execute(select(func.count(Task.id)))).scalar_one()
    assert count == 0


@pytest.mark.openhands
def test_installed_openhands_sdk_extra_is_detected() -> None:
    pytest.importorskip("openhands.sdk")
    pytest.importorskip("openhands.tools.preset.default")
    from app.agents.openhands import _MODULE_CACHE, _module_available

    _MODULE_CACHE.clear()
    assert _module_available("openhands.sdk") is True
    assert _module_available("openhands.tools.preset.default") is True
