"""Workflow runtime services outside LangGraph orchestration (WP7).

This module owns persistence, state transitions, workflow events, execution
completion signalling, memory injection and task-less ask cleanup. None of
those responsibilities require LangGraph; keeping them here lets the graph
manager concentrate on topology, routing and node coordination.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.task_states import TaskStateMachine, TaskStatus
from app.events import types as event_types
from app.events.bus import EventBus
from app.events.store import EventStore
from app.git.workspace import GitWorktreeService
from app.models import Artifact, Execution, Project, Task
from app.roles.definitions import RoleDefinition
from app.services.memory import MemoryService
from app.services.workflow import ASK_ALLOWED_ROLES, WorkflowError

logger = logging.getLogger("sceneworks.workflows.runtime")

_TERMINAL_EXECUTION_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}


class WorkflowRuntime:
    """Provider-independent runtime primitives used by workflow orchestration."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        git: GitWorktreeService,
        bus: EventBus,
        event_store: EventStore,
        memory_service: MemoryService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._git = git
        self._bus = bus
        self._events = event_store
        self._memory = memory_service
        self._pending_executions: dict[str, asyncio.Event] = {}

    # --------------------------------------------------------- model access

    async def get_task(self, session: AsyncSession, task_id: int) -> Task:
        task = await session.get(Task, task_id)
        if task is None:
            raise WorkflowError(f"task {task_id} not found", 404)
        return task

    async def get_project(self, session: AsyncSession, project_id: int) -> Project:
        project = await session.get(Project, project_id)
        if project is None:
            raise WorkflowError(f"project {project_id} not found", 404)
        return project

    # ------------------------------------------------------ task transitions

    async def transition(
        self,
        task: Task,
        action: str,
        actor: str,
        session: AsyncSession,
    ) -> TaskStatus:
        current = TaskStatus(task.status)
        try:
            new_status = TaskStateMachine.transition(current, action)
        except Exception as exc:
            raise WorkflowError(
                f"invalid transition: task is {current.value}, "
                f"cannot apply action {action!r}",
                409,
            ) from exc
        task.status = new_status.value
        task.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await self._publish_transition(
            task.id,
            current.value,
            new_status.value,
            action,
            actor,
        )
        return new_status

    async def set_task_status(
        self,
        task_id: int,
        status: TaskStatus,
        actor: str = "system",
    ) -> None:
        async with self._session_factory() as session:
            task = await self.get_task(session, task_id)
            try:
                existing = TaskStatus(task.status).value
            except ValueError:
                existing = "unknown"
            task.status = status.value
            task.updated_at = datetime.now(timezone.utc)
            await session.commit()
        await self._publish_transition(
            task_id,
            existing,
            status.value,
            "workflow",
            actor,
        )

    async def _publish_transition(
        self,
        task_id: int,
        before: str,
        after: str,
        action: str,
        actor: str,
    ) -> None:
        payload = {"from": before, "to": after, "action": action, "actor": actor}
        row = await self._events.append(
            execution_id=None,
            task_id=task_id,
            type=event_types.TASK_TRANSITIONED,
            payload=payload,
        )
        await self._bus.publish(
            {
                "id": row.id,
                "execution_id": None,
                "task_id": task_id,
                "type": event_types.TASK_TRANSITIONED,
                "payload": payload,
                "severity": "info",
                "timestamp": row.timestamp.isoformat(),
            }
        )

    # ---------------------------------------------------------- executions

    async def create_execution(
        self,
        *,
        task: Task,
        role: RoleDefinition,
        workspace: dict,
        system_prompt: str,
        user_prompt: str,
    ) -> Execution:
        execution = Execution(
            id=uuid.uuid4().hex,
            task_id=task.id,
            role=role.key,
            backend=role.backend,
            model_profile=role.model_profile,
            status="QUEUED",
            workspace=workspace,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_preview=user_prompt[:2000],
        )
        async with self._session_factory() as session:
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
        return execution

    async def wait_for_execution(self, execution_id: str) -> Execution:
        event = asyncio.Event()
        self._pending_executions[execution_id] = event
        try:
            async with self._session_factory() as session:
                row = await session.get(Execution, execution_id)
                if row is None:
                    raise RuntimeError(f"execution {execution_id} vanished")
                if row.status in _TERMINAL_EXECUTION_STATUSES:
                    event.set()
            await event.wait()
            async with self._session_factory() as session:
                row = await session.get(Execution, execution_id)
                if row is None:
                    raise RuntimeError(f"execution {execution_id} vanished")
                return row
        finally:
            self._pending_executions.pop(execution_id, None)

    async def on_execution_finished(self, execution_id: str) -> None:
        """Bridge ExecutionEngine completion into workflow/runtime state."""
        async with self._session_factory() as session:
            execution = await session.get(Execution, execution_id)
            if execution is None:
                return
            task = None
            if execution.task_id is not None:
                task = await session.get(Task, execution.task_id)
            if task is None and execution.role in ASK_ALLOWED_ROLES:
                await self._store_company_artifact(session, execution)

        if task is None:
            await self._cleanup_ask_worktree(execution)

        event = self._pending_executions.get(execution_id)
        if event:
            event.set()

    # ------------------------------------------------------------- events

    async def append_task_note(self, task_id: int, title: str, detail: str) -> None:
        await self._events.append(
            execution_id=None,
            task_id=task_id,
            type="task.note",
            payload={"title": title, "detail": detail[:4000]},
        )

    async def emit_workflow_event(
        self,
        task_id: int,
        event_type: str,
        payload: dict,
    ) -> None:
        row = await self._events.append(
            execution_id=None,
            task_id=task_id,
            type=event_type,
            payload=payload,
        )
        await self._bus.publish(
            {
                "id": row.id,
                "execution_id": None,
                "task_id": task_id,
                "type": event_type,
                "payload": payload,
                "severity": "info",
                "timestamp": row.timestamp.isoformat(),
            }
        )

    # ------------------------------------------------------------- memory

    async def inject_memory(
        self,
        project_id: int,
        task_description: str,
        types: list[str] | None = None,
    ) -> dict:
        if self._memory is None:
            return {"memories": [], "proposed": [], "injected_ids": []}
        return await self._memory.injection_context(
            project_id,
            task_description,
            types=types,
        )

    async def emit_memory_injection(
        self,
        task_id: int,
        node: str,
        ctx: dict,
    ) -> None:
        await self.emit_workflow_event(
            task_id,
            event_types.MEMORY_INJECTED,
            {
                "node": node,
                "injected_ids": ctx.get("injected_ids", []),
                "query_terms": ctx.get("query_terms", []),
                "truncated": ctx.get("truncated", False),
                "retrieval": [
                    item.get("retrieval", {}) for item in ctx.get("memories", [])
                ],
                "proposed_not_injected": [
                    item["id"] for item in ctx.get("proposed", [])
                ],
            },
        )

    @staticmethod
    def permission_names(role: RoleDefinition) -> list[str]:
        return sorted(permission.value for permission in role.permissions)

    # ----------------------------------------------------- task-less asks

    async def _store_company_artifact(
        self,
        session: AsyncSession,
        execution: Execution,
    ) -> None:
        workspace = execution.workspace or {}
        title = workspace.get("ask_title") or f"{execution.role} decision"
        content = execution.result or f"Execution failed: {execution.error or 'unknown'}"
        base_commit = workspace.get("base_commit")
        if base_commit:
            content = f"_Analyzed repository snapshot: `{base_commit}`_\n\n" + content
        artifact = Artifact(
            kind="company_decision",
            role=execution.role,
            project_id=(
                workspace.get("project_id")
                if isinstance(workspace.get("project_id"), int)
                else None
            ),
            title=title,
            content=content,
            source_execution_id=execution.id,
        )
        session.add(artifact)
        await session.commit()
        await self._events.append(
            execution_id=execution.id,
            task_id=None,
            type=event_types.ARTIFACT_CREATED,
            payload={
                "artifact_id": artifact.id,
                "role": execution.role,
                "base_commit": base_commit,
            },
        )

    async def _cleanup_ask_worktree(self, execution: Execution) -> None:
        workspace = execution.workspace or {}
        path = workspace.get("ask_worktree_path")
        repo = workspace.get("repo_path")
        if not path or not repo:
            return
        try:
            await self._git.remove_worktree(Path(repo), Path(path), None)
        except Exception:  # noqa: BLE001
            logger.warning("ask worktree cleanup failed for execution %s", execution.id)
