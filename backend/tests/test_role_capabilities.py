"""WP10 role persona/capability and domain-evidence regression tests."""

from __future__ import annotations

from types import SimpleNamespace

from app.models import Project, Task
from app.roles.capabilities import resolve_capabilities
from app.roles.definitions import default_roles
from app.workflows.role_runtime import WorkflowRoleRuntime


def _role(key: str):
    return next(role for role in default_roles() if role.key == key)


def _keys(items) -> set[str]:
    return {item.key for item in items}


def test_engineer_is_systems_oriented_but_not_permanently_automotive():
    engineer = _role("engineer")

    core = set(engineer.core_capabilities)
    assert {
        "software-engineering",
        "systems-engineering",
        "black-box-thinking",
        "interface-design",
        "requirements-verification",
        "root-cause-debugging",
        "testing",
        "performance-engineering",
        "api-design",
    } <= core

    # PCS expertise is a project/task overlay. Making it part of the generic
    # Engineer would silently bias every future SceneWorks-managed repository.
    assert core.isdisjoint(
        {
            "automotive-sensor-systems",
            "lidar",
            "radar",
            "automotive-diagnostics-uds",
            "mbse",
            "sysml",
        }
    )


def test_architect_and_reviewer_have_systems_engineering_lenses():
    architect = _role("architect")
    reviewer = _role("reviewer")

    assert {"systems-engineering", "black-box-thinking", "interface-design"} <= set(
        architect.core_capabilities
    )
    assert {
        "systems-engineering",
        "black-box-thinking",
        "requirements-verification",
        "independent-verification",
    } <= set(reviewer.core_capabilities)


def test_capability_resolution_layers_project_role_and_task_overlays():
    engineer = _role("engineer")
    project = SimpleNamespace(
        capability_profile={
            "domains": ["automotive-sensor-systems"],
            "skills": ["real-time-data-pipelines"],
            "methods": [],
            "roles": {
                "engineer": {
                    "domains": ["lidar"],
                    "skills": [],
                    "methods": [],
                }
            },
        }
    )
    task = SimpleNamespace(
        capability_requirements={
            "domains": ["automotive-diagnostics-uds"],
            "skills": [],
            "methods": ["mbse", "sysml"],
            "roles": {
                "engineer": {
                    "domains": ["lidar"],  # duplicate must not be repeated
                    "skills": [],
                    "methods": [],
                }
            },
        }
    )

    resolved = resolve_capabilities(engineer, project, task)

    assert "systems-engineering" in _keys(resolved.core)
    assert _keys(resolved.project) == {
        "automotive-sensor-systems",
        "real-time-data-pipelines",
        "lidar",
    }
    assert _keys(resolved.task) == {
        "automotive-diagnostics-uds",
        "mbse",
        "sysml",
    }
    assert len([item for item in resolved.all if item.key == "lidar"]) == 1


def test_unknown_project_capability_is_supported_without_becoming_fact():
    engineer = _role("engineer")
    project = SimpleNamespace(
        capability_profile={
            "domains": ["future-specialized-sensor"],
            "roles": {},
        }
    )

    resolved = resolve_capabilities(engineer, project, None)
    custom = next(item for item in resolved.project if item.key == "future-specialized-sensor")

    assert custom.kind == "custom"
    rendered = resolved.render().lower()
    assert "source of project-specific truth" in rendered
    assert "repository evidence" in rendered


async def test_prompt_injects_project_domain_and_task_methods_only_when_configured(
    context, tmp_path
):
    project = Project(
        name="sensor-platform",
        description="sensor processing tool",
        repository_path=str(tmp_path),
        default_branch="main",
        architecture_context_paths=[],
        test_commands=[],
        build_commands=[],
        capability_profile={
            "skills": [],
            "domains": ["automotive-sensor-systems", "lidar"],
            "methods": [],
            "roles": {},
        },
    )
    task = Task(
        project_id=1,
        title="diagnostic change",
        description="Expose one diagnostic configuration path",
        status="NEW",
        priority="medium",
        engineering_contract={},
        capability_requirements={
            "skills": [],
            "domains": ["automotive-diagnostics-uds"],
            "methods": ["mbse"],
            "roles": {},
        },
        advisory_results={},
    )
    role = context.roles.effective("engineer")

    built = await context.prompt_builder.build(
        role=role,
        project=project,
        task=task,
        workspace={
            "cwd": str(tmp_path),
            "repo_path": str(tmp_path),
            "branch": "test",
            "base_commit": "abc",
            "permissions": [],
        },
    )

    assert "senior systems-oriented software engineer" in built.system
    assert "Systems engineering" in built.system
    assert "Automotive sensor systems" in built.system
    assert "LiDAR systems" in built.system
    assert "Automotive diagnostics / UDS" in built.system
    assert "Model-Based Systems Engineering (MBSE)" in built.system
    assert "SysML" not in built.system


async def test_project_and_task_capability_profiles_round_trip_through_api(
    client, git_repo
):
    project_response = await client.post(
        "/api/projects",
        json={
            "name": "PCS-like project",
            "repository_path": str(git_repo),
            "capability_profile": {
                "skills": ["real-time-data-pipelines"],
                "domains": ["automotive-sensor-systems", "lidar"],
                "methods": [],
                "roles": {
                    "technical_expert": {
                        "skills": [],
                        "domains": ["point-cloud-processing"],
                        "methods": [],
                    }
                },
            },
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    assert project["capability_profile"]["domains"] == [
        "automotive-sensor-systems",
        "lidar",
    ]

    task_response = await client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "title": "UDS config",
            "description": "Implement a diagnostics configuration path",
            "capability_requirements": {
                "skills": [],
                "domains": ["automotive-diagnostics-uds"],
                "methods": [],
                "roles": {},
            },
        },
    )
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    assert task["capability_requirements"]["domains"] == [
        "automotive-diagnostics-uds"
    ]

    replacement = await client.put(
        f"/api/tasks/{task['id']}/capabilities",
        json={
            "skills": [],
            "domains": ["automotive-diagnostics-uds"],
            "methods": ["mbse", "sysml"],
            "roles": {},
        },
    )
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["capability_requirements"]["methods"] == [
        "mbse",
        "sysml",
    ]


async def test_role_api_exposes_professional_identity(client):
    response = await client.get("/api/roles")
    assert response.status_code == 200
    roles = {row["key"]: row for row in response.json()}

    assert "systems-oriented" in roles["engineer"]["persona"]
    assert "systems-engineering" in roles["engineer"]["core_capabilities"]
    assert "lidar" not in roles["engineer"]["core_capabilities"]


class _RuntimeStub:
    async def get_task(self, session, task_id):
        task = await session.get(Task, task_id)
        assert task is not None
        return task


async def test_original_technical_expert_evidence_survives_architect_and_reaches_execution_roles(
    context, git_repo
):
    async with context.engine_factory() as session:
        project = Project(
            name="sensor-project",
            repository_path=str(git_repo),
            default_branch="main",
            architecture_context_paths=[],
            test_commands=[],
            build_commands=[],
            capability_profile={
                "skills": [],
                "domains": ["automotive-sensor-systems", "lidar"],
                "methods": [],
                "roles": {},
            },
        )
        session.add(project)
        await session.flush()
        task = Task(
            project_id=project.id,
            title="sensor change",
            description="Change one sensor-processing behavior",
            status="NEW",
            priority="medium",
            engineering_contract={},
            capability_requirements={},
            advisory_results={},
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    role_runtime = WorkflowRoleRuntime(
        context.engine_factory,
        engine=None,
        git=None,
        prompts=None,
        roles=None,
        event_store=None,
        runtime=_RuntimeStub(),
    )
    marker = "DOMAIN-CONSTRAINT: preserve calibrated range units exactly"
    await role_runtime.store_task_advisory_result(task_id, "technical_expert", marker)

    # Simulate finish_architect(), which owns/replaces architecture_result. The
    # original domain evidence must remain independently persisted.
    async with context.engine_factory() as session:
        task = await session.get(Task, task_id)
        task.architecture_result = "ARCHITECT: keep the public API stable"
        await session.commit()

    async with context.engine_factory() as session:
        task = await session.get(Task, task_id)
        project = await session.get(Project, task.project_id)
        assert task.advisory_results["technical_expert"] == marker

        workspace = {
            "cwd": str(git_repo),
            "repo_path": str(git_repo),
            "branch": "test",
            "base_commit": "abc",
            "permissions": [],
        }
        engineer_prompt = await context.prompt_builder.build(
            role=context.roles.effective("engineer"),
            project=project,
            task=task,
            workspace=workspace,
            context_worktree_path=str(git_repo),
        )
        reviewer_prompt = await context.prompt_builder.build(
            role=context.roles.effective("reviewer"),
            project=project,
            task=task,
            workspace=workspace,
            context_worktree_path=str(git_repo),
        )

    assert marker in engineer_prompt.user
    assert marker in reviewer_prompt.user
    assert "ARCHITECT: keep the public API stable" in engineer_prompt.user
    assert "ARCHITECT: keep the public API stable" in reviewer_prompt.user
