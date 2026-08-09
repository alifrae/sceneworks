"""Company role services: manual "ask" invocations and stored decisions.

V1 supports manual invocation of company roles (Ask CEO/CTO/Product/GTM/...
Architect). Their outputs are stored as company artifacts/decisions. Company
role invocations never trigger chains of other agents and never modify code.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.models import Execution, Project, Task
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleNotFoundError, RoleRegistry
from app.services.workflow import ASK_ALLOWED_ROLES, WorkflowError, TaskWorkflowService

logger = logging.getLogger("sceneworks.company")


class CompanyService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        workflow: TaskWorkflowService,
        roles: RoleRegistry,
        git: GitWorktreeService,
        prompt_builder: PromptBuilder,
        engine: ExecutionEngine,
    ):
        self._session_factory = session_factory
        self._workflow = workflow
        self._roles = roles
        self._git = git
        self._prompts = prompt_builder
        self._engine = engine

    async def ask(self, role_key: str, project_id: int | None, question: str) -> Execution:
        if role_key not in ASK_ALLOWED_ROLES:
            raise WorkflowError(
                f"role {role_key!r} cannot be invoked manually; "
                f"allowed: {', '.join(sorted(ASK_ALLOWED_ROLES))}",
                400,
            )
        if not question or not question.strip():
            raise WorkflowError("question must not be empty", 400)
        try:
            role = self._roles.effective(role_key)
        except RoleNotFoundError as exc:
            raise WorkflowError(str(exc), 404) from exc

        project: Project | None = None
        if project_id is not None:
            async with self._session_factory() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    raise WorkflowError(f"project {project_id} not found", 404)

        repo = Path(project.repository_path).resolve() if project else None
        base: str | None = None
        if repo is not None:
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(f"repository at {repo} is not a valid Git repository", 400)
            base = await self._git.resolve_base_commit(repo, project.default_branch)

        permissions = sorted(p.value for p in role.permissions)
        workspace = {
            "cwd": str(repo) if repo else None,
            "repo_path": str(repo) if repo else None,
            "branch": project.default_branch if project else None,
            "base_commit": base,
            "permissions": permissions,
            "project_id": project_id,
            "ask_title": question[:200],
        }
        prompt = await self._prompts.build(
            role=role,
            project=project,
            task=None,
            workspace=workspace,
            extra={"Question": question},
        )
        execution = await self._workflow.create_execution(
            task=None,
            project=project,
            role=role,
            workspace=workspace,
            system_prompt=prompt.system,
            user_prompt=prompt.user,
        )
        await self._engine.start(execution.id)
        return execution

