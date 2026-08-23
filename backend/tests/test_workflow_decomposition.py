"""WP7 workflow-manager responsibility boundary tests."""

from __future__ import annotations

import inspect

from app.workflows import WorkflowManager as PublicWorkflowManager
from app.workflows.control import WorkflowControl
from app.workflows.manager import WorkflowManager as GraphWorkflowManager
from app.workflows.orchestrator import WorkflowManager as OrchestratedWorkflowManager
from app.workflows.recovery import WorkflowRecovery
from app.workflows.role_runtime import WorkflowRoleRuntime
from app.workflows.runtime import WorkflowRuntime
import app.workflows.role_runtime as role_runtime_module
import app.workflows.runtime as runtime_module


def test_public_workflow_manager_is_the_decomposed_orchestrator():
    assert PublicWorkflowManager is OrchestratedWorkflowManager
    assert PublicWorkflowManager is not GraphWorkflowManager
    assert issubclass(PublicWorkflowManager, GraphWorkflowManager)


def test_non_graph_runtimes_have_no_langgraph_dependency():
    """Persistence and role execution mechanics remain framework-neutral."""
    assert "langgraph" not in inspect.getsource(runtime_module).lower()
    assert "langgraph" not in inspect.getsource(role_runtime_module).lower()


async def test_application_context_wires_wp7_components(context):
    manager = context.workflow_manager
    assert type(manager) is OrchestratedWorkflowManager
    assert isinstance(manager._runtime, WorkflowRuntime)
    assert isinstance(manager._roles_runtime, WorkflowRoleRuntime)
    assert isinstance(manager._control, WorkflowControl)
    assert isinstance(manager._recovery, WorkflowRecovery)
    assert context.execution_engine.on_execution_finished == manager.on_execution_finished


async def test_public_control_still_uses_existing_task_state_contract(
    client, context, git_repo
):
    project = await client.post(
        "/api/projects",
        json={"name": "wp7-control", "repository_path": str(git_repo)},
    )
    assert project.status_code == 201, project.text
    task = await client.post(
        "/api/tasks",
        json={"project_id": project.json()["id"], "title": "decomposition contract"},
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]

    # Use the extracted controller through the public manager. Stub graph launch
    # so this test isolates the command/state boundary rather than re-testing
    # LangGraph execution, which is covered by test_workflow_graph.py.
    async def no_graph(*args, **kwargs):
        return None

    context.workflow_manager._launch_graph = no_graph
    await context.workflow_manager.start_workflow(task_id)
    await context.workflow_manager.wait_until_idle(task_id)

    response = await client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "ARCHITECTURE_ANALYSIS"

    events = await client.get(f"/api/tasks/{task_id}/events")
    event_types = [event["type"] for event in events.json()]
    assert "task.transitioned" in event_types
    assert "workflow.started" in event_types
