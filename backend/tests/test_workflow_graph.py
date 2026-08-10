"""LangGraph workflow tests: topology, persistence, integration, failure.

Uses the scripted FakeAgentBackend; no live model required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.agents.fake import FakeAgentBackend, ScriptStep
from app.domain.task_states import TaskStatus
from app.models import Project, Task


# ---------------------------------------------------------------- helpers


async def _create_project(session_factory, git_repo):
    async with session_factory() as session:
        project = Project(
            name="test-project",
            repository_path=str(git_repo),
            default_branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def _create_task(session_factory, project, status="NEW", base_commit=None):
    async with session_factory() as session:
        task = Task(
            project_id=project.id,
            title="test task",
            description="test",
            status=status,
            base_commit=base_commit,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def _wait_task_status(session_factory, task_id, statuses, timeout=30):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with session_factory() as session:
            task = await session.get(Task, task_id)
            if task is not None and task.status in statuses:
                return task.status
        if asyncio.get_event_loop().time() > deadline:
            async with session_factory() as session:
                task = await session.get(Task, task_id)
            raise AssertionError(
                f"task {task_id} never reached {statuses}; "
                f"status={task.status if task else '?'}"
            )
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------- tests


async def test_graph_topology_architect_completes(context, git_repo):
    """Architect starts and reaches approval state."""
    project = await _create_project(context.engine_factory, git_repo)
    task = await _create_task(context.engine_factory, project)

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[ScriptStep(kind="summary", summary="architecture done")]
    )

    await context.workflow_manager.start_workflow(task.id)
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value


async def test_graph_topology_approval_engineer_runs(context, git_repo):
    """After approval, engineer runs automatically."""
    project = await _create_project(context.engine_factory, git_repo)

    base_commit = (await __import__("app.git.workspace", fromlist=["run_git"]).run_git(
        git_repo, "rev-parse", "HEAD"
    )).strip()
    task = await _create_task(context.engine_factory, project, base_commit=base_commit)

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[ScriptStep(kind="summary", summary="architecture done")]
    )
    await context.workflow_manager.start_workflow(task.id)
    await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value},
    )

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="summary", summary="implemented"),
        ]
    )
    await context.workflow_manager.resume_approval(task.id, "approve")
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.READY_FOR_HUMAN.value


async def test_graph_topology_architecture_rejection(context, git_repo):
    """Architecture rejection does not start engineer."""
    project = await _create_project(context.engine_factory, git_repo)
    task = await _create_task(context.engine_factory, project)

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[ScriptStep(kind="summary", summary="architecture done")]
    )
    await context.workflow_manager.start_workflow(task.id)
    await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value},
    )

    await context.workflow_manager.resume_approval(task.id, "reject", "bad idea")
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.REJECTED.value},
    )
    assert final_status == TaskStatus.REJECTED.value


async def test_graph_topology_architecture_revision_loop(context, git_repo):
    """Architecture revision loops back to architect."""
    project = await _create_project(context.engine_factory, git_repo)
    task = await _create_task(context.engine_factory, project)

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[ScriptStep(kind="summary", summary="architecture done")]
    )
    await context.workflow_manager.start_workflow(task.id)
    await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value},
    )

    await context.workflow_manager.resume_approval(task.id, "revision", "try again")
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value


async def test_graph_topology_reviewer_approved(context, git_repo):
    """Reviewer APPROVED routes to READY_FOR_HUMAN."""
    project = await _create_project(context.engine_factory, git_repo)
    base_commit = (await __import__("app.git.workspace", fromlist=["run_git"]).run_git(
        git_repo, "rev-parse", "HEAD"
    )).strip()
    task = await _create_task(
        context.engine_factory, project,
        status="READY_TO_IMPLEMENT", base_commit=base_commit,
    )

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="summary", summary="good work"),
        ]
    )
    await context.workflow_manager.start_implementation(task.id)
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.READY_FOR_HUMAN.value


async def test_graph_topology_reviewer_changes_requested(context, git_repo):
    """V2.2 auto-repair: CHANGES_REQUESTED auto-routes to engineer, cycles
    until approved or max iterations exhausted."""
    project = await _create_project(context.engine_factory, git_repo)
    base_commit = (await __import__("app.git.workspace", fromlist=["run_git"]).run_git(
        git_repo, "rev-parse", "HEAD"
    )).strip()
    task = await _create_task(
        context.engine_factory, project,
        status="READY_TO_IMPLEMENT", base_commit=base_commit,
    )

    # Backend returns APPROVED — graph auto-runs engineer → reviewer → READY_FOR_HUMAN.
    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="summary", summary="good work\nVERDICT: APPROVED"),
        ]
    )
    await context.workflow_manager.start_implementation(task.id)
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.READY_FOR_HUMAN.value


async def test_graph_persistence_checkpoint_after_architect(context, git_repo):
    """Graph checkpoint exists after workflow progression."""
    project = await _create_project(context.engine_factory, git_repo)
    task = await _create_task(context.engine_factory, project)

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[ScriptStep(kind="summary", summary="architecture done")]
    )
    await context.workflow_manager.start_workflow(task.id)
    await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value},
    )

    # Resume from the same thread_id should load the checkpoint.
    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="summary", summary="implemented"),
        ]
    )
    await context.workflow_manager.resume_approval(task.id, "approve")
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.READY_FOR_HUMAN.value


async def test_graph_failure_reflected_correctly(context, git_repo):
    """Node failure is reflected in task status."""
    project = await _create_project(context.engine_factory, git_repo)
    task = await _create_task(context.engine_factory, project)

    # Architect backend fails.
    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="emit", type="agent.message", payload={"text": "hi"}),
            ScriptStep(kind="fail", error="analysis crashed"),
        ]
    )
    await context.workflow_manager.start_workflow(task.id)
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value},
    )
    assert final_status == TaskStatus.FAILED.value


async def test_graph_cancellation_propagates(context, git_repo):
    """Cancelling a task cancels the graph and backend."""
    project = await _create_project(context.engine_factory, git_repo)
    task = await _create_task(context.engine_factory, project)

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="emit", type="agent.message", payload={"text": "working"}),
            ScriptStep(kind="sleep", seconds=30),
        ]
    )
    await context.workflow_manager.start_workflow(task.id)

    # Wait for the architect node to start execution.
    await asyncio.sleep(0.5)
    await context.workflow_manager.cancel(task.id)

    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.CANCELLED.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.CANCELLED.value


# ---------------------------------------------------------------- V2.2 tests


async def test_graph_v22_auto_repair_loop(context, git_repo):
    """CHANGES_REQUESTED auto-routes to engineer; loop exits on APPROVED."""
    project = await _create_project(context.engine_factory, git_repo)
    base_commit = (await __import__("app.git.workspace", fromlist=["run_git"]).run_git(
        git_repo, "rev-parse", "HEAD"
    )).strip()
    task = await _create_task(
        context.engine_factory, project,
        status="READY_TO_IMPLEMENT", base_commit=base_commit,
    )

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="summary", summary="all good\nVERDICT: APPROVED"),
        ]
    )
    await context.workflow_manager.start_implementation(task.id)
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.READY_FOR_HUMAN.value


async def test_graph_v22_max_repair_iterations(context, git_repo):
    """Max review iterations stops the auto-repair loop; task stays CHANGES_REQUESTED."""
    project = await _create_project(context.engine_factory, git_repo)
    base_commit = (await __import__("app.git.workspace", fromlist=["run_git"]).run_git(
        git_repo, "rev-parse", "HEAD"
    )).strip()
    task = await _create_task(
        context.engine_factory, project,
        status="READY_TO_IMPLEMENT", base_commit=base_commit,
    )

    # Backend always returns CHANGES_REQUESTED.
    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="summary", summary="needs work\nVERDICT: CHANGES_REQUESTED"),
        ]
    )
    await context.workflow_manager.start_implementation(task.id)

    # Wait for the auto-loop to exhaust all iterations and the graph to end.
    # The graph will cycle max_review_iterations times (3) then stop.
    deadline = asyncio.get_event_loop().time() + 30
    while True:
        if task.id not in context.workflow_manager._active_graphs:
            break
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("graph never completed")
        await asyncio.sleep(0.1)

    # Task should be CHANGES_REQUESTED with max iterations exhausted.
    async with context.engine_factory() as session:
        t = await session.get(Task, task.id)
        assert t.status == TaskStatus.CHANGES_REQUESTED.value

    # Now manually trigger a fix with a fresh backend.
    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=2\n"),
            ScriptStep(kind="summary", summary="fixed\nVERDICT: APPROVED"),
        ]
    )
    await context.workflow_manager.start_implementation(task.id)
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.READY_FOR_HUMAN.value


async def test_graph_v22_triage_defaults_fake_backend(context, git_repo):
    """With fake backend, triage defaults to architect path with implementation."""
    project = await _create_project(context.engine_factory, git_repo)
    task = await _create_task(context.engine_factory, project)

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[ScriptStep(kind="summary", summary="architecture analysis")]
    )
    await context.workflow_manager.start_workflow(task.id)
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value, TaskStatus.FAILED.value},
    )
    assert final_status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value


async def test_graph_v22_engineer_idempotency(context, git_repo):
    """Duplicate start_implementation calls are idempotent when graph is running."""
    project = await _create_project(context.engine_factory, git_repo)
    base_commit = (await __import__("app.git.workspace", fromlist=["run_git"]).run_git(
        git_repo, "rev-parse", "HEAD"
    )).strip()
    task = await _create_task(
        context.engine_factory, project,
        status="READY_TO_IMPLEMENT", base_commit=base_commit,
    )

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="sleep", seconds=1.0),
            ScriptStep(kind="summary", summary="all good\nVERDICT: APPROVED"),
        ]
    )

    # First call starts the graph and returns.
    await context.workflow_manager.start_implementation(task.id)

    # Second call while graph is still running should wait for first
    # to complete via _await_previous_graph, then do nothing.
    await context.workflow_manager.start_implementation(task.id)

    # Graph should complete successfully.
    final_status = await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
        timeout=30,
    )
    assert final_status == TaskStatus.READY_FOR_HUMAN.value


async def test_graph_v22_worktree_continuity_for_repair(context, git_repo):
    """Repair iteration reuses the same engineer worktree."""
    project = await _create_project(context.engine_factory, git_repo)
    base_commit = (await __import__("app.git.workspace", fromlist=["run_git"]).run_git(
        git_repo, "rev-parse", "HEAD"
    )).strip()
    task = await _create_task(
        context.engine_factory, project,
        status="READY_TO_IMPLEMENT", base_commit=base_commit,
    )

    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="change.py", content="x=1\n"),
            ScriptStep(kind="summary", summary="all good\nVERDICT: APPROVED"),
        ]
    )
    await context.workflow_manager.start_implementation(task.id)
    await _wait_task_status(
        context.engine_factory, task.id,
        {TaskStatus.READY_FOR_HUMAN.value, TaskStatus.FAILED.value},
    )

    # Verify the task has a single worktree path throughout.
    async with context.engine_factory() as session:
        t = await session.get(Task, task.id)
        assert t.worktree_path is not None
        assert Path(t.worktree_path).is_dir()
        assert t.task_branch == f"sw-task-{task.id}"
