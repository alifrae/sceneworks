"""Restart and recovery tests (WP3).

`app/execution/recovery.py` is a documentation module: 100 lines of claims about
what survives a restart, with no code and — until now — nothing asserting any of
it (docs/wp0-baseline-audit.md, F15). These tests make the doc enforceable.

The three defects WP1's qualification suite found are guarded here directly:

- a clean shutdown recorded its executions as CANCELLED, indistinguishable from a
  user cancellation and invisible to reconciliation;
- a task could be stranded in a running state forever, because reconciliation
  keyed off execution status and a clean shutdown had already made those
  terminal;
- the LangGraph checkpointer was closed underneath running graphs.

The invariant behind all of them: **after a restart, no task may claim work is in
progress.** Nothing is running, so such a state is a false statement about the
project that leaves an operator unable to tell whether to wait or to retry.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.agents.fake import FakeAgentBackend, ScriptStep
from app.context import build_context
from app.domain.task_states import TaskStatus
from app.execution.engine import ACTIVE_STATUSES, RUNNING_TASK_STATES
from app.models import Execution, Project, Task


async def _project(session_factory, git_repo) -> Project:
    async with session_factory() as session:
        project = Project(
            name="recovery-test",
            repository_path=str(git_repo),
            default_branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def _task(session_factory, project, status="NEW", **fields) -> Task:
    async with session_factory() as session:
        task = Task(
            project_id=project.id, title="t", description="d",
            status=status, **fields,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def _execution(session_factory, task, status="RUNNING", role="architect") -> Execution:
    import uuid

    async with session_factory() as session:
        execution = Execution(
            id=uuid.uuid4().hex, task_id=task.id, role=role,
            backend="fake", status=status, workspace={},
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        return execution


async def _reload(session_factory, task_id) -> Task:
    async with session_factory() as session:
        return await session.get(Task, task_id)


# --------------------------------------------- pass 1: active executions


async def test_active_executions_become_interrupted(context, git_repo):
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="ARCHITECTURE_ANALYSIS")
    for status in ACTIVE_STATUSES:
        await _execution(context.engine_factory, task, status=status)

    interrupted = await context.execution_engine.recover_interrupted()

    assert len(interrupted) == len(ACTIVE_STATUSES)
    async with context.engine_factory() as session:
        rows = (await session.execute(
            select(Execution).where(Execution.task_id == task.id)
        )).scalars().all()
    assert {r.status for r in rows} == {"INTERRUPTED"}
    assert all("interrupted by SceneWorks restart" in (r.error or "") for r in rows)


async def test_interrupted_execution_fails_its_running_task(context, git_repo):
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="IMPLEMENTING")
    await _execution(context.engine_factory, task, status="RUNNING")

    await context.execution_engine.recover_interrupted()

    assert (await _reload(context.engine_factory, task.id)).status == "FAILED"


async def test_completed_executions_are_left_alone(context, git_repo):
    """Finished work is not re-run and not re-marked."""
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="READY_FOR_HUMAN")
    done = await _execution(context.engine_factory, task, status="COMPLETED")

    await context.execution_engine.recover_interrupted()

    async with context.engine_factory() as session:
        assert (await session.get(Execution, done.id)).status == "COMPLETED"
    assert (await _reload(context.engine_factory, task.id)).status == "READY_FOR_HUMAN"


# ------------------------------- pass 2: tasks stranded in a running state


@pytest.mark.parametrize("status", sorted(RUNNING_TASK_STATES))
async def test_task_running_with_no_active_execution_is_reconciled(
    context, git_repo, status,
):
    """REGRESSION: a clean shutdown left tasks running forever.

    A clean shutdown finalizes its own executions, so pass 1 finds nothing
    active and the task used to keep claiming ARCHITECTURE_ANALYSIS with no
    agent, no graph and no retry path — shown in the UI as permanently working.
    """
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status=status)
    # Already terminal, exactly as a clean shutdown leaves it.
    await _execution(context.engine_factory, task, status="INTERRUPTED")

    await context.execution_engine.recover_interrupted()

    reloaded = await _reload(context.engine_factory, task.id)
    assert reloaded.status == "FAILED", (
        f"a task in {status} with nothing running must be reconciled"
    )
    assert reloaded.current_role is None
    assert reloaded.current_execution_id is None


async def test_reconciled_task_is_retryable(context, git_repo):
    """FAILED is the state `retry` accepts; reconciliation must land there."""
    from app.domain.task_states import TaskStateMachine

    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="IMPLEMENTING")
    await _execution(context.engine_factory, task, status="INTERRUPTED")

    await context.execution_engine.recover_interrupted()

    reloaded = await _reload(context.engine_factory, task.id)
    assert TaskStateMachine.can_transition(
        TaskStatus(reloaded.status), "retry"
    ) or TaskStateMachine.can_transition(
        TaskStatus(reloaded.status), "retry_architecture"
    )


async def test_task_with_a_genuinely_active_execution_is_not_reconciled(
    context, git_repo,
):
    """Pass 2 must not fail a task whose work pass 1 is about to interrupt."""
    project = await _project(context.engine_factory, git_repo)
    running = await _task(context.engine_factory, project, status="REVIEWING")
    await _execution(context.engine_factory, running, status="RUNNING")

    await context.execution_engine.recover_interrupted()

    # Pass 1 interrupts it and fails the task — once, not twice.
    reloaded = await _reload(context.engine_factory, running.id)
    assert reloaded.status == "FAILED"


@pytest.mark.parametrize(
    "status",
    ["AWAITING_ARCHITECTURE_APPROVAL", "CHANGES_REQUESTED", "READY_FOR_HUMAN",
     "READY_TO_IMPLEMENT", "ACCEPTED", "REJECTED", "NEW"],
)
async def test_human_waiting_states_are_never_touched(context, git_repo, status):
    """Nothing about a human-waiting state depends on a live process."""
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status=status)

    await context.execution_engine.recover_interrupted()

    assert (await _reload(context.engine_factory, task.id)).status == status


async def test_committed_work_survives_reconciliation(context, git_repo):
    """Recovery must never discard a result commit or a task branch."""
    project = await _project(context.engine_factory, git_repo)
    task = await _task(
        context.engine_factory, project, status="IMPLEMENTING",
        base_commit="a" * 40, result_commit="b" * 40, task_branch="sw-task-1",
        worktree_path=str(git_repo),
    )
    await _execution(context.engine_factory, task, status="RUNNING")

    await context.execution_engine.recover_interrupted()

    reloaded = await _reload(context.engine_factory, task.id)
    assert reloaded.result_commit == "b" * 40
    assert reloaded.task_branch == "sw-task-1"
    assert reloaded.base_commit == "a" * 40
    assert reloaded.worktree_path == str(git_repo)


# ---------------------------------------------------------------- shutdown


async def test_shutdown_records_interrupted_not_cancelled(settings, git_repo):
    """REGRESSION: a shutdown-cancelled execution looked like a user cancellation.

    Conflating the two told an operator "somebody cancelled this" when the truth
    was "the process stopped underneath it", and hid the work from restart
    reconciliation, which looks for interrupted executions.
    """
    context = await build_context(settings)
    context.backends.register(
        "fake",
        FakeAgentBackend(steps=[ScriptStep(kind="sleep", seconds=30)]),
    )
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="ARCHITECTURE_ANALYSIS")
    execution = await _execution(context.engine_factory, task, status="QUEUED")

    await context.execution_engine.start(execution.id)
    # Let it reach the backend before tearing down.
    for _ in range(100):
        async with context.engine_factory() as session:
            if (await session.get(Execution, execution.id)).status in ("STARTING", "RUNNING"):
                break
        await asyncio.sleep(0.05)

    await context.shutdown()

    engine_factory = context.engine_factory
    async with engine_factory() as session:
        row = await session.get(Execution, execution.id)
    assert row.status == "INTERRUPTED", (
        f"a shutdown must not look like a user cancellation; got {row.status}"
    )
    assert "shutdown" in (row.error or "").lower()


async def test_restart_leaves_no_task_claiming_to_run(settings, git_repo):
    """End to end: shut down mid-execution, rebuild, assert an honest state."""
    context = await build_context(settings)
    context.backends.register(
        "fake",
        FakeAgentBackend(steps=[ScriptStep(kind="sleep", seconds=30)]),
    )
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="ARCHITECTURE_ANALYSIS")
    execution = await _execution(context.engine_factory, task, status="QUEUED")
    await context.execution_engine.start(execution.id)
    for _ in range(100):
        async with context.engine_factory() as session:
            if (await session.get(Execution, execution.id)).status in ("STARTING", "RUNNING"):
                break
        await asyncio.sleep(0.05)

    await context.shutdown()
    rebuilt = await build_context(settings)
    try:
        reloaded = await _reload(rebuilt.engine_factory, task.id)
        assert reloaded.status not in RUNNING_TASK_STATES, (
            f"after a restart nothing is executing, so {reloaded.status} is a "
            "false claim of progress"
        )
        async with rebuilt.engine_factory() as session:
            row = await session.get(Execution, execution.id)
        assert row.status in ("INTERRUPTED", "CANCELLED", "FAILED", "COMPLETED")
    finally:
        await rebuilt.shutdown()


async def test_shutdown_cancels_graphs_before_closing_the_checkpointer(
    settings, git_repo,
):
    """REGRESSION: graphs wrote to a closed aiosqlite handle.

    Closing the checkpointer first surfaced as `ValueError: no active connection`
    from inside LangGraph — an opaque error telling an operator nothing about
    what happened to their task.
    """
    context = await build_context(settings)
    context.backends.register(
        "fake",
        FakeAgentBackend(steps=[ScriptStep(kind="sleep", seconds=30)]),
    )
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project)

    await context.workflow_manager.start_workflow(task.id)
    await asyncio.sleep(0.5)
    assert context.workflow_manager.has_active_graph(task.id)

    # Must not raise, and must leave no graph behind.
    await context.shutdown()

    assert not context.workflow_manager.has_active_graph(task.id)


async def test_recovery_is_idempotent(context, git_repo):
    """Reconciling twice must not cascade further state changes."""
    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="IMPLEMENTING")
    await _execution(context.engine_factory, task, status="RUNNING")

    await context.execution_engine.recover_interrupted()
    first = (await _reload(context.engine_factory, task.id)).status
    second_pass = await context.execution_engine.recover_interrupted()

    assert second_pass == [], "nothing is left active after the first pass"
    assert (await _reload(context.engine_factory, task.id)).status == first


async def test_recovery_emits_events_for_what_it_changed(context, git_repo):
    """A silent state change is undiagnosable; recovery must leave a trace."""
    from app.events import types as event_types

    project = await _project(context.engine_factory, git_repo)
    task = await _task(context.engine_factory, project, status="IMPLEMENTING")
    execution = await _execution(context.engine_factory, task, status="RUNNING")

    await context.execution_engine.recover_interrupted()

    events = await context.event_store.list_for_task(task.id, limit=100)
    types = {e.type for e in events}
    assert event_types.TASK_TRANSITIONED in types
    exec_events = await context.event_store.list_for_execution(execution.id, limit=100)
    assert event_types.EXECUTION_INTERRUPTED in {e.type for e in exec_events}
