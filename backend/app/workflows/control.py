"""Public workflow control surface separated from LangGraph node logic (WP7).

The controller owns founder/system commands and active graph scheduling. It
uses the orchestrator only for graph execution and runtime primitives; it does
not define graph topology or node behavior.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.domain.task_states import RUNNING, TaskStateMachine, TaskStatus
from app.services.workflow import WorkflowError
from app.workflows.state import InitiativeState


class WorkflowControl:
    def __init__(self, owner: Any) -> None:
        self._owner = owner

    async def start_workflow(self, task_id: int) -> None:
        owner = self._owner
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            await owner._transition(task, "start_architecture", "founder", session)
            project_id = task.project_id

        config = owner._config(task_id)
        initial: InitiativeState = {
            "task_id": task_id,
            "project_id": project_id,
            "task_status": TaskStatus.ARCHITECTURE_ANALYSIS.value,
            "request_type": None,
            "triage_executed": False,
            "triage_result": None,
            "requires_implementation": True,
            "product_executed": False,
            "product_execution_id": None,
            "product_result": None,
            "cto_executed": False,
            "cto_execution_id": None,
            "cto_result": None,
            "technical_expert_executed": False,
            "technical_expert_execution_id": None,
            "technical_expert_result": None,
            "architecture_execution_id": None,
            "implementation_execution_id": None,
            "review_execution_id": None,
            "architecture_result": None,
            "implementation_result": None,
            "review_result": None,
            "review_iteration": 0,
            "error": None,
        }
        await owner._emit_workflow_event(
            task_id,
            "workflow.started",
            {"task_id": task_id},
        )
        graph_task = asyncio.create_task(
            owner._launch_graph(initial, config, task_id),
            name=f"wf-{task_id}",
        )
        owner._active_graphs[task_id] = graph_task

    async def resume_approval(
        self,
        task_id: int,
        action: str,
        reason: str = "",
    ) -> None:
        owner = self._owner
        await self.await_previous_graph(task_id)
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            if action == "approve":
                await owner._transition(task, "approve_architecture", "founder", session)
            elif action == "reject":
                await owner._transition(task, "reject_architecture", "founder", session)
            elif action == "revision":
                await owner._transition(
                    task,
                    "request_architecture_revision",
                    "founder",
                    session,
                )

        decision = {"action": action}
        if action == "reject":
            decision["reason"] = reason
        elif action == "revision":
            decision["notes"] = reason
        graph_task = asyncio.create_task(
            owner._launch_graph(Command(resume=decision), owner._config(task_id), task_id),
            name=f"wf-{task_id}-resume",
        )
        owner._active_graphs[task_id] = graph_task

    async def start_implementation(self, task_id: int) -> None:
        owner = self._owner
        await self.await_previous_graph(task_id)
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            if task.status != TaskStatus.IMPLEMENTING.value:
                if TaskStateMachine.can_transition(
                    TaskStatus(task.status),
                    "start_implementation",
                ):
                    await owner._transition(
                        task,
                        "start_implementation",
                        "founder",
                        session,
                    )
                elif task.status in (
                    TaskStatus.TESTING.value,
                    TaskStatus.REVIEWING.value,
                    TaskStatus.READY_FOR_HUMAN.value,
                    TaskStatus.ACCEPTED.value,
                ):
                    return
                else:
                    raise WorkflowError(
                        f"invalid transition: task is {task.status}, "
                        "cannot start implementation",
                        409,
                    )

        cmd = Command(
            update={
                "task_id": task_id,
                "project_id": task.project_id,
                "task_status": TaskStatus.IMPLEMENTING.value,
                "error": None,
            },
            goto="route_entry",
        )
        graph_task = asyncio.create_task(
            owner._launch_graph(cmd, owner._config(task_id), task_id),
            name=f"wf-{task_id}-impl",
        )
        owner._active_graphs[task_id] = graph_task

    async def start_review(self, task_id: int) -> None:
        owner = self._owner
        await self.await_previous_graph(task_id)
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            if task.status != TaskStatus.REVIEWING.value:
                await owner._transition(task, "start_review", "founder", session)

        cmd = Command(
            update={
                "task_id": task_id,
                "project_id": task.project_id,
                "task_status": TaskStatus.REVIEWING.value,
                "error": None,
            },
            goto="route_entry",
        )
        graph_task = asyncio.create_task(
            owner._launch_graph(cmd, owner._config(task_id), task_id),
            name=f"wf-{task_id}-review",
        )
        owner._active_graphs[task_id] = graph_task

    async def accept(self, task_id: int) -> None:
        owner = self._owner
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            await owner._transition(task, "accept", "founder", session)
        if owner._verification is not None:
            await owner._verification.capture_resolution(task_id)

    async def reject(self, task_id: int, reason: str = "") -> None:
        owner = self._owner
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            await owner._transition(task, "reject", "founder", session)
        if reason:
            await owner._append_task_note(task_id, "work rejected", reason)

    async def send_back_to_engineer(self, task_id: int, notes: str = "") -> None:
        owner = self._owner
        await self.await_previous_graph(task_id)
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            await owner._transition(
                task,
                "send_back_to_engineer",
                "founder",
                session,
            )
            project_id = task.project_id
        if notes:
            await owner._append_task_note(task_id, "sent back to engineer", notes)

        cmd = Command(
            update={
                "task_id": task_id,
                "project_id": project_id,
                "task_status": TaskStatus.CHANGES_REQUESTED.value,
                "review_iteration": 0,
                "error": None,
            },
            goto="route_entry",
        )
        graph_task = asyncio.create_task(
            owner._launch_graph(cmd, owner._config(task_id), task_id),
            name=f"wf-{task_id}-sendback",
        )
        owner._active_graphs[task_id] = graph_task

    async def cancel(self, task_id: int) -> None:
        owner = self._owner
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            execution_id = task.current_execution_id
            await owner._transition(task, "cancel", "founder", session)
        if execution_id:
            await owner._engine.cancel(execution_id)
        graph_task = owner._active_graphs.pop(task_id, None)
        if graph_task and not graph_task.done():
            graph_task.cancel()

    async def retry(self, task_id: int) -> None:
        owner = self._owner
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            if task.status != TaskStatus.FAILED.value:
                raise WorkflowError("retry is only available for FAILED tasks", 409)
            resume_implementation = bool(task.architecture_result)
            await owner._transition(
                task,
                "retry" if resume_implementation else "retry_architecture",
                "founder",
                session,
            )
        if resume_implementation:
            await self.start_implementation(task_id)
        else:
            await self.start_workflow(task_id)

    async def cleanup_worktree(self, task_id: int) -> None:
        owner = self._owner
        async with owner._session_factory() as session:
            task = await owner._get_task(session, task_id)
            if task.status in RUNNING:
                raise WorkflowError("cannot clean up while the task is running", 409)
            repo = (await owner._get_project(session, task.project_id)).repository_path
            worktree_path = task.worktree_path
            branch = task.task_branch
            task.worktree_path = None
            task.task_branch = None
            await session.commit()
        if worktree_path:
            await owner._git.remove_worktree(Path(repo), Path(worktree_path), branch)
        await owner._append_task_note(task_id, "worktree cleaned up", "")

    def has_active_graph(self, task_id: int) -> bool:
        graph_task = self._owner._active_graphs.get(task_id)
        return graph_task is not None and not graph_task.done()

    async def wait_until_idle(self, task_id: int) -> None:
        await self.await_previous_graph(task_id)

    async def await_previous_graph(self, task_id: int) -> None:
        previous = self._owner._active_graphs.get(task_id)
        if previous is not None and not previous.done():
            try:
                await previous
            except Exception:
                pass
