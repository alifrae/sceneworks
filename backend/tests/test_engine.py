"""Execution engine tests: lifecycle, events, cancellation, recovery.

Uses the scripted FakeAgentBackend; no live model access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.agents.fake import FakeAgentBackend, ScriptStep
from app.events import types as event_types
from app.models import Event, Execution, Project, Task
from app.roles.definitions import default_roles


async def _create_execution(
    context, *, role="engineer", workspace=None, steps=None, summary="done", task_id=None
) -> str:
    role_def = next(r for r in default_roles() if r.key == role)
    backend = FakeAgentBackend(steps or [ScriptStep(kind="summary", summary=summary)])
    context.backends._backends["fake"] = backend  # replace scripted backend
    async with context.engine_factory() as session:
        execution = Execution(
            id="e" * 32 if task_id is None else f"e{task_id}0" * 8,
            task_id=task_id,
            role=role,
            backend="fake",
            model_profile=role_def.model_profile,
            status="QUEUED",
            workspace=workspace or {"cwd": ".", "repo_path": "."},
            system_prompt="system",
            user_prompt="user",
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        return execution.id


async def _wait_status(context, execution_id: str, expected: str, timeout: float = 15):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with context.engine_factory() as session:
            row = await session.get(Execution, execution_id)
            if row is not None and row.status == expected:
                return
        if asyncio.get_event_loop().time() > deadline:
            async with context.engine_factory() as session:
                row = await session.get(Execution, execution_id)
            raise AssertionError(
                f"execution {execution_id} never reached {expected}; "
                f"status={row.status if row else None}, error={row.error if row else None}"
            )
        await asyncio.sleep(0.05)


async def test_execution_completes_and_persists_events(context):
    execution_id = await _create_execution(context)
    await context.execution_engine.start(execution_id)
    await _wait_status(context, execution_id, "COMPLETED")

    async with context.engine_factory() as session:
        row = await session.get(Execution, execution_id)
        assert row.status == "COMPLETED"
        assert row.result == "done"
        assert row.started_at is not None
        assert row.finished_at is not None
        events = (
            await session.execute(
                select(Event).where(Event.execution_id == execution_id).order_by(Event.id)
            )
        ).scalars().all()
    types = [e.type for e in events]
    assert event_types.EXECUTION_STARTED in types
    assert event_types.EXECUTION_COMPLETED in types
    assert all(e.execution_id == execution_id for e in events)
    assert all(e.timestamp is not None for e in events)


async def test_failed_execution(context):
    execution_id = await _create_execution(
        context,
        steps=[
            ScriptStep(kind="emit", type="agent.message", payload={"text": "hi"}),
            ScriptStep(kind="fail", error="tests failed"),
        ],
    )
    await context.execution_engine.start(execution_id)
    await _wait_status(context, execution_id, "FAILED")
    async with context.engine_factory() as session:
        row = await session.get(Execution, execution_id)
        assert row.error == "tests failed"


async def test_cancellation(context):
    execution_id = await _create_execution(
        context,
        steps=[
            ScriptStep(kind="emit", type="agent.message", payload={"text": "working"}),
            ScriptStep(kind="sleep", seconds=30),
        ],
    )
    await context.execution_engine.start(execution_id)
    await _wait_status(context, execution_id, "STARTING", timeout=5)
    cancelled = await context.execution_engine.cancel(execution_id)
    assert cancelled
    await _wait_status(context, execution_id, "CANCELLED", timeout=10)
    async with context.engine_factory() as session:
        row = await session.get(Execution, execution_id)
        assert row.status == "CANCELLED"


async def test_cancel_inactive_execution_returns_false(context):
    assert await context.execution_engine.cancel("nope") is False


async def test_restart_recovery_marks_running_executions(context):
    execution_id = await _create_execution(context, steps=[ScriptStep(kind="sleep", seconds=30)])
    async with context.engine_factory() as session:
        row = await session.get(Execution, execution_id)
        row.status = "RUNNING"
        task = Task(project_id=1, title="t", status="IMPLEMENTING")
        session.add(task)
        await session.commit()
        task_id = task.id
    interrupted = await context.execution_engine.recover_interrupted()
    assert execution_id in interrupted
    async with context.engine_factory() as session:
        row = await session.get(Execution, execution_id)
        assert row.status == "INTERRUPTED"
        task_row = await session.get(Task, task_id)
        assert task_row.status == "FAILED"


async def test_engine_is_active_tracking(context):
    execution_id = await _create_execution(
        context, steps=[ScriptStep(kind="sleep", seconds=5)]
    )
    await context.execution_engine.start(execution_id)
    assert execution_id in context.execution_engine.active_ids()
    await _wait_status(context, execution_id, "COMPLETED", timeout=15)
    deadline = asyncio.get_event_loop().time() + 10
    while execution_id in context.execution_engine.active_ids():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("execution never left the active set")
        await asyncio.sleep(0.05)
    assert execution_id not in context.execution_engine.active_ids()


async def test_workflow_continuation_runs(context, git_repo):
    """Engine's on_execution_finished hook transitions the task state and the
    Engineer's worktree flow works end to end."""
    context.backends._backends["fake"] = FakeAgentBackend(
        steps=[
            ScriptStep(kind="file", path="fix.py", content="x = 2\n"),
            ScriptStep(kind="commit", message="task: fix"),
            ScriptStep(kind="summary", summary="implemented"),
        ]
    )
    async with context.engine_factory() as session:
        project = Project(
            name="p", repository_path=str(git_repo), default_branch="main"
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        task = Task(project_id=project.id, title="t", status="READY_TO_IMPLEMENT")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    execution = await context.workflow.start_implementation(task_id)
    await _wait_status(context, execution.id, "COMPLETED", timeout=15)
    # The continuation hook runs after finalization; poll for the task state.
    deadline = asyncio.get_event_loop().time() + 10
    while True:
        async with context.engine_factory() as session:
            task_row = await session.get(Task, task_id)
            if task_row.status == "TESTING":
                break
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"task never reached TESTING; status={task_row.status}")
        await asyncio.sleep(0.05)
    async with context.engine_factory() as session:
        task_row = await session.get(Task, task_id)
        assert task_row.status == "TESTING"
        assert task_row.result_commit
        assert task_row.implementation_summary == "implemented"
        assert task_row.worktree_path
    # Change landed in the worktree, never in the human tree.
    assert not (git_repo / "fix.py").exists()
    assert (Path(task_row.worktree_path) / "fix.py").read_text() == "x = 2\n"
