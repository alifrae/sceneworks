"""Thin public WorkflowManager facade for WP7.

LangGraph topology and node coordination remain in the legacy graph manager for
this migration step. Runtime/persistence mechanics, founder-facing commands,
and restart recovery are delegated to focused components. Callers keep the
same WorkflowManager API while responsibilities move behind stable boundaries.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.domain.task_states import TaskStatus
from app.events.bus import EventBus
from app.events.store import EventStore
from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.models import Execution, Project, Task
from app.roles.definitions import RoleDefinition
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry
from app.services.memory import MemoryService
from app.workflows.control import WorkflowControl
from app.workflows.manager import (
    MAX_REVIEW_ITERATIONS_DEFAULT,
    WorkflowManager as GraphWorkflowManager,
)
from app.workflows.recovery import WorkflowRecovery
from app.workflows.runtime import WorkflowRuntime


class WorkflowManager(GraphWorkflowManager):
    """Compatibility facade with explicit WP7 component boundaries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: ExecutionEngine,
        git: GitWorktreeService,
        prompt_builder: PromptBuilder,
        roles: RoleRegistry,
        bus: EventBus,
        event_store: EventStore,
        settings: Settings,
        checkpoint_db_path: str = "data/workflow_checkpoints.db",
        max_review_iterations: int = MAX_REVIEW_ITERATIONS_DEFAULT,
        memory_service: MemoryService | None = None,
    ) -> None:
        super().__init__(
            session_factory,
            engine,
            git,
            prompt_builder,
            roles,
            bus,
            event_store,
            settings,
            checkpoint_db_path=checkpoint_db_path,
            max_review_iterations=max_review_iterations,
            memory_service=memory_service,
        )
        self._runtime = WorkflowRuntime(
            session_factory,
            git,
            bus,
            event_store,
            memory_service,
        )
        self._control = WorkflowControl(self)
        self._recovery = WorkflowRecovery(self)

    # --------------------------------------------------------- engine bridge

    async def on_execution_finished(self, execution_id: str) -> None:
        await self._runtime.on_execution_finished(execution_id)

    async def _wait_for_execution(self, execution_id: str) -> Execution:
        return await self._runtime.wait_for_execution(execution_id)

    # ----------------------------------------------------------- persistence

    async def _get_task(self, session: AsyncSession, task_id: int) -> Task:
        return await self._runtime.get_task(session, task_id)

    async def _get_project(self, session: AsyncSession, project_id: int) -> Project:
        return await self._runtime.get_project(session, project_id)

    async def _transition(
        self,
        task: Task,
        action: str,
        actor: str,
        session: AsyncSession,
    ) -> TaskStatus:
        return await self._runtime.transition(task, action, actor, session)

    async def _set_task_status(
        self,
        task_id: int,
        status: TaskStatus,
        actor: str = "system",
    ) -> None:
        await self._runtime.set_task_status(task_id, status, actor)

    async def _create_execution(
        self,
        *,
        task: Task,
        role: RoleDefinition,
        workspace: dict,
        system_prompt: str,
        user_prompt: str,
    ) -> Execution:
        return await self._runtime.create_execution(
            task=task,
            role=role,
            workspace=workspace,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    async def _append_task_note(self, task_id: int, title: str, detail: str) -> None:
        await self._runtime.append_task_note(task_id, title, detail)

    def _permission_names(self, role: RoleDefinition) -> list[str]:
        return self._runtime.permission_names(role)

    async def _emit_workflow_event(
        self,
        task_id: int,
        event_type: str,
        payload: dict,
    ) -> None:
        await self._runtime.emit_workflow_event(task_id, event_type, payload)

    async def _inject_memory(
        self,
        project_id: int,
        task_description: str,
        types: list[str] | None = None,
    ) -> dict:
        return await self._runtime.inject_memory(
            project_id,
            task_description,
            types=types,
        )

    async def _emit_memory_injection(
        self,
        task_id: int,
        node: str,
        ctx: dict,
    ) -> None:
        await self._runtime.emit_memory_injection(task_id, node, ctx)

    # ------------------------------------------------------ public controls

    async def start_workflow(self, task_id: int) -> None:
        await self._control.start_workflow(task_id)

    async def resume_approval(
        self,
        task_id: int,
        action: str,
        reason: str = "",
    ) -> None:
        await self._control.resume_approval(task_id, action, reason)

    async def start_implementation(self, task_id: int) -> None:
        await self._control.start_implementation(task_id)

    async def start_review(self, task_id: int) -> None:
        await self._control.start_review(task_id)

    async def accept(self, task_id: int) -> None:
        await self._control.accept(task_id)

    async def reject(self, task_id: int, reason: str = "") -> None:
        await self._control.reject(task_id, reason)

    async def send_back_to_engineer(self, task_id: int, notes: str = "") -> None:
        await self._control.send_back_to_engineer(task_id, notes)

    async def cancel(self, task_id: int) -> None:
        await self._control.cancel(task_id)

    async def retry(self, task_id: int) -> None:
        await self._control.retry(task_id)

    async def cleanup_worktree(self, task_id: int) -> None:
        await self._control.cleanup_worktree(task_id)

    def has_active_graph(self, task_id: int) -> bool:
        return self._control.has_active_graph(task_id)

    async def wait_until_idle(self, task_id: int) -> None:
        await self._control.wait_until_idle(task_id)

    async def _await_previous_graph(self, task_id: int) -> None:
        await self._control.await_previous_graph(task_id)

    # ------------------------------------------------------------ recovery

    async def recover_workflows(self) -> list[int]:
        return await self._recovery.recover()
