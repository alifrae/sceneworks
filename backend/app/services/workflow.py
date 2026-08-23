"""Task workflow utilities (preparation helpers, state transitions).

LangGraph handles task workflow orchestration. This module retains shared
utilities used by CompanyService and API-level task operations.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.model_routing import ModelRouter
from app.config.settings import Settings
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
    """Shared utility layer for non-orchestration workflow operations."""

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
        model_router: ModelRouter | None = None,
    ):
        self._session_factory = session_factory
        self.engine = engine
        self._git = git
        self._prompts = prompt_builder
        self._roles = roles
        self._bus = bus
        self._events = event_store
        self._settings = settings
        self._model_router = model_router or ModelRouter(settings, ())

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
        resolution = self._model_router.resolve(role)
        execution = Execution(
            id=uuid.uuid4().hex,
            task_id=task.id if task else None,
            role=role.key,
            backend=resolution.backend,
            model_profile=resolution.profile,
            model_name=resolution.model,
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


def parse_review_verdict(text: str) -> str:
    match = re.search(r"VERDICT\s*:\s*(APPROVED|CHANGES_REQUESTED)", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    if "CHANGES_REQUESTED" in text.upper():
        return "CHANGES_REQUESTED"
    if not text.strip():
        return "CHANGES_REQUESTED"
    return "APPROVED"


def _cap(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore") + "\n[truncated]"
