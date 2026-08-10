"""Task workflow utilities (preparation helpers, state transitions).

LangGraph now handles task workflow orchestration (WorkflowManager).
This module retains shared utilities used by CompanyService, API-level
task operations, and the WorkflowManager's internal methods.

Do not add new task orchestration logic here — use the LangGraph
WorkflowManager for that.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.domain.task_states import (
    RUNNING,
    InvalidTransition,
    TaskStateMachine,
    TaskStatus,
)
from app.events import types as event_types
from app.events.bus import EventBus
from app.events.store import EventStore
from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.models import Execution, Project, Task
from app.roles.definitions import RoleDefinition
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry

logger = logging.getLogger("sceneworks.workflow")

COMPANY_ROLES = {"ceo", "cto", "product", "gtm", "technical_expert"}
ASK_ALLOWED_ROLES = {"ceo", "cto", "product", "gtm", "architect", "technical_expert"}


class WorkflowError(Exception):
    """Domain error surfaced to the API as 4xx."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class TaskWorkflowService:
    """Shared utility layer for task workflow operations.

    Task orchestration is now handled by LangGraph (WorkflowManager).
    This class retains helpers for creating executions, managing
    worktrees, and transitioning task states — used by both the
    WorkflowManager and the CompanyService.
    """

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
    ):
        self._session_factory = session_factory
        self.engine = engine
        self._git = git
        self._prompts = prompt_builder
        self._roles = roles
        self._bus = bus
        self._events = event_store
        self._settings = settings

    # -------------------------------------------------------------- helpers

    async def _get_task(self, session: AsyncSession, task_id: int) -> Task:
        task = await session.get(Task, task_id)
        if task is None:
            raise WorkflowError(f"task {task_id} not found", 404)
        return task

    async def _get_project(self, session: AsyncSession, project_id: int) -> Project:
        project = await session.get(Project, project_id)
        if project is None:
            raise WorkflowError(f"project {project_id} not found", 404)
        return project

    async def _transition(
        self,
        task: Task,
        action: str,
        actor: str,
        session: AsyncSession,
    ) -> TaskStatus:
        current = TaskStatus(task.status)
        try:
            new_status = TaskStateMachine.transition(current, action)
        except InvalidTransition as exc:
            raise WorkflowError(
                f"invalid transition: task is {current.value}, "
                f"cannot apply action {action!r}",
                409,
            ) from exc
        task.status = new_status.value
        task.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await self._events.append(
            execution_id=None,
            task_id=task.id,
            type=event_types.TASK_TRANSITIONED,
            payload={
                "from": current.value,
                "to": new_status.value,
                "action": action,
                "actor": actor,
            },
        )
        await self._bus.publish(
            {
                "id": 0,
                "execution_id": None,
                "task_id": task.id,
                "type": event_types.TASK_TRANSITIONED,
                "payload": {
                    "from": current.value,
                    "to": new_status.value,
                    "action": action,
                    "actor": actor,
                },
                "severity": "info",
            }
        )
        return new_status

    async def create_execution(
        self,
        *,
        task: Task | None,
        project: Project | None,
        role: RoleDefinition,
        workspace: dict,
        system_prompt: str,
        user_prompt: str,
    ) -> Execution:
        execution = Execution(
            id=uuid.uuid4().hex,
            task_id=task.id if task else None,
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

    def permission_names(self, role: RoleDefinition) -> list[str]:
        return sorted(p.value for p in role.permissions)

    async def _append_task_note(self, task_id: int, title: str, detail: str) -> None:
        await self._events.append(
            execution_id=None,
            task_id=task_id,
            type="task.note",
            payload={"title": title, "detail": detail[:4000]},
        )

    # -------------------------------------------------------- worktree cleanup

    async def cleanup_worktree(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status in RUNNING:
                raise WorkflowError("cannot clean up while the task is running", 409)
            repo = (await self._get_project(session, task.project_id)).repository_path
            from pathlib import Path

            worktree_path = task.worktree_path
            branch = task.task_branch
            task.worktree_path = None
            task.task_branch = None
            await session.commit()
        if worktree_path:
            await self._git.remove_worktree(Path(repo), Path(worktree_path), branch)
        await self._append_task_note(task_id, "worktree cleaned up", "")


def parse_review_verdict(text: str) -> str:
    match = re.search(r"VERDICT\s*:\s*(APPROVED|CHANGES_REQUESTED)", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    if "CHANGES_REQUESTED" in text.upper():
        return "CHANGES_REQUESTED"
    return "APPROVED"


def _cap(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore") + "\n[truncated]"
