"""Public WorkflowManager composition facade for WP7.

The graph core owns only LangGraph topology/checkpointing/routing. Operational
responsibilities are delegated to provider-independent components while this
class preserves the WorkflowManager API used by FastAPI, evaluation and tests.
"""

from __future__ import annotations

from pathlib import Path

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
from app.workflows.advisory_runtime import WorkflowAdvisoryRuntime
from app.workflows.control import WorkflowControl
from app.workflows.graph_core import (
    MAX_REVIEW_ITERATIONS_DEFAULT,
    GraphWorkflowManager,
)
from app.workflows.recovery import WorkflowRecovery
from app.workflows.role_runtime import WorkflowRoleRuntime
from app.workflows.runtime import WorkflowRuntime
from app.workflows.state import InitiativeState


class WorkflowManager(GraphWorkflowManager):
    """Stable workflow API composed from focused WP7 components."""

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
            checkpoint_db_path=checkpoint_db_path,
            max_review_iterations=max_review_iterations,
        )
        # Retain these public-manager attributes for command scheduling and for
        # compatibility with existing tests/diagnostics. The graph core itself
        # does not depend on them.
        self._engine = engine
        self._git = git
        self._settings = settings

        self._runtime = WorkflowRuntime(
            session_factory,
            git,
            bus,
            event_store,
            memory_service,
        )
        self._roles_runtime = WorkflowRoleRuntime(
            session_factory,
            engine,
            git,
            prompt_builder,
            roles,
            event_store,
            self._runtime,
        )
        self._advisory_runtime = WorkflowAdvisoryRuntime(
            session_factory,
            engine,
            git,
            prompt_builder,
            roles,
            self._runtime,
            self._roles_runtime,
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

    # ----------------------------------------------- triage/advisory runtime

    async def _run_triage(self, state: InitiativeState) -> InitiativeState:
        return await self._advisory_runtime.run_triage(state)

    async def _run_advisory_role(
        self,
        state: InitiativeState,
        role_key: str,
        display: str,
    ) -> InitiativeState:
        return await self._advisory_runtime.run_advisory_role(
            state,
            role_key,
            display,
        )

    # ------------------------------------------------------ role lifecycle

    async def _prepare_and_start_architect(
        self,
        task_id: int,
        state: InitiativeState | None = None,
    ) -> Execution:
        return await self._roles_runtime.prepare_and_start_architect(task_id, state)

    async def _prepare_and_start_engineer(
        self,
        task_id: int,
        is_correction: bool = False,
    ) -> Execution:
        return await self._roles_runtime.prepare_and_start_engineer(
            task_id,
            is_correction,
        )

    async def _prepare_and_start_reviewer(self, task_id: int) -> Execution:
        return await self._roles_runtime.prepare_and_start_reviewer(task_id)

    async def _finish_architect(
        self,
        task_id: int,
        execution: Execution,
        requires_implementation: bool = True,
    ) -> None:
        await self._roles_runtime.finish_architect(
            task_id,
            execution,
            requires_implementation,
        )

    async def _finish_engineer(self, task_id: int, execution: Execution) -> None:
        await self._roles_runtime.finish_engineer(task_id, execution)

    async def _capture_engineer_commit(
        self,
        task_id: int,
        worktree: Path,
        base_commit: str | None,
    ) -> tuple[str | None, bool]:
        return await self._roles_runtime.capture_engineer_commit(
            task_id,
            worktree,
            base_commit,
        )

    async def _finish_reviewer(
        self,
        task_id: int,
        execution: Execution,
        verdict: str,
    ) -> None:
        await self._roles_runtime.finish_reviewer(task_id, execution, verdict)

    async def _store_task_advisory_result(
        self,
        task_id: int,
        role_key: str,
        content: str,
    ) -> None:
        await self._roles_runtime.store_task_advisory_result(
            task_id,
            role_key,
            content,
        )

    async def _cleanup_review_worktree(self, task: Task) -> None:
        await self._roles_runtime.cleanup_review_worktree(task)

    async def _approve_architecture_impl(self, task_id: int) -> None:
        await self._roles_runtime.approve_architecture(task_id)

    async def _reject_architecture_impl(self, task_id: int, reason: str) -> None:
        await self._roles_runtime.reject_architecture(task_id, reason)

    async def _request_revision_impl(self, task_id: int, notes: str) -> None:
        await self._roles_runtime.request_revision(task_id, notes)

    async def _cleanup_architect_worktree(self, task_id: int) -> None:
        await self._roles_runtime.cleanup_architect_worktree(task_id)

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
