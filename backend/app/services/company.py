"""Company role services: manual "ask" invocations and stored decisions.

V1 supports manual invocation of company roles (Ask CEO/CTO/Product/GTM/...
Architect). Their outputs are stored as company artifacts/decisions. Company
role invocations never trigger chains of other agents and never modify code.
"""

from __future__ import annotations

import logging
import asyncio
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.models import Execution, Project
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleNotFoundError, RoleRegistry
from app.services.policy import ProjectPolicyService, render_policy
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
        policy_service: ProjectPolicyService | None = None,
    ):
        self._session_factory = session_factory
        self._workflow = workflow
        self._roles = roles
        self._git = git
        self._prompts = prompt_builder
        self._engine = engine
        self._policy = policy_service

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
        # Repository-grounded asks obey the same snapshot invariant as workflow
        # roles: the agent reads a commit-pinned detached worktree, never the
        # human working tree. Uncommitted edits can therefore never enter the
        # answer, and the analyzed commit is recorded on the execution.
        if repo is not None:
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(f"repository at {repo} is not a valid Git repository", 400)
            base = await self._git.resolve_base_commit(repo, project.default_branch)
        permissions = sorted(p.value for p in role.permissions)
        workspace = {
            "cwd": None,
            "repo_path": str(repo) if repo else None,
            "branch": None,
            "base_commit": base,
            "permissions": permissions,
            "project_id": project_id,
            "ask_title": question[:200],
            # Recorded so on_execution_finished can remove the snapshot.
            "ask_worktree_path": None,
            "preparing": True,
        }
        # A manual ask acknowledges as soon as the durable execution exists.
        # Snapshot creation, prompt/context preparation and agent startup are
        # deliberately outside the HTTP request so a slow repository cannot
        # freeze the Company page.
        execution = await self._workflow.create_execution(
            task=None,
            project=project,
            role=role,
            workspace=workspace,
            system_prompt="Preparing repository context…",
            user_prompt=question,
        )
        asyncio.create_task(
            self._prepare_and_start(
                execution.id, role, project, repo, base, question, workspace
            ),
            name=f"company-ask-{execution.id}",
        )
        return execution

    async def _prepare_and_start(
        self,
        execution_id: str,
        role,
        project: Project | None,
        repo: Path | None,
        base: str | None,
        question: str,
        workspace: dict,
    ) -> None:
        worktree_path: Path | None = None
        try:
            if repo is not None and base is not None:
                label = f"{project.id if project else 'company'}-{uuid.uuid4().hex[:10]}"
                snapshot = await self._git.create_snapshot_worktree(repo, base, label)
                worktree_path = snapshot.worktree_path
                workspace = {
                    **workspace,
                    "cwd": str(worktree_path),
                    "ask_worktree_path": str(worktree_path),
                    "preparing": False,
                }
            else:
                workspace = {**workspace, "preparing": False}

            policy_kwargs: dict = {}
            if self._policy is not None and project is not None:
                policy_row = await self._policy.get(project.id)
                if policy_row is not None:
                    policy_kwargs = {
                        "policy_text": render_policy(policy_row),
                        "policy_file_paths": policy_row.policy_file_paths,
                    }

            prompt = await self._prompts.build(
                role=role,
                project=project,
                task=None,
                workspace=workspace,
                extra={"Question": question},
                context_worktree_path=str(worktree_path) if worktree_path else None,
                **policy_kwargs,
            )
            async with self._session_factory() as session:
                execution = await session.get(Execution, execution_id)
                if execution is None:
                    raise RuntimeError(f"execution {execution_id} disappeared during preparation")
                execution.workspace = workspace
                execution.system_prompt = prompt.system
                execution.user_prompt = prompt.user
                execution.prompt_preview = prompt.user[:2000]
                await session.commit()
            await self._engine.start(execution_id)
        except Exception as exc:  # noqa: BLE001 - surface async preparation failures
            logger.exception("company ask preparation failed for %s", execution_id)
            if worktree_path is not None and repo is not None:
                try:
                    await self._git.remove_worktree(repo, worktree_path, None)
                except Exception:  # noqa: BLE001
                    logger.warning("could not clean up ask worktree %s", worktree_path)
            await self._engine.fail_before_start(execution_id, str(exc))
