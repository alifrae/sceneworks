"""Task workflow orchestration.

Business rules live here, not in API handlers. The workflow service:

- validates actions against the task state machine;
- creates and isolates Git worktrees per phase;
- builds prompts and creates Execution rows;
- continues the workflow when executions finish (Architect -> approval ->
  Engineer -> review -> human gate).

The human founder is the only approver in V1; roles never auto-chain.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
from app.models import Artifact, Execution, Project, Task
from app.roles.definitions import RoleDefinition
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry

logger = logging.getLogger("sceneworks.workflow")

COMPANY_ROLES = {"ceo", "cto", "product", "gtm"}
ASK_ALLOWED_ROLES = {"ceo", "cto", "product", "gtm", "architect"}


class WorkflowError(Exception):
    """Domain error surfaced to the API as 4xx."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class TaskWorkflowService:
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

    # ------------------------------------------------------- task: Architect

    async def start_architecture(self, task_id: int) -> Execution:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)
            await self._transition(task, "start_architecture", "founder", session)

            repo = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(
                    f"repository at {repo} is not a valid Git repository: {info.error}",
                    400,
                )
            base = await self._git.resolve_base_commit(repo, project.default_branch)
            worktree = await self._git.create_detached_worktree(
                repo, base, task.id
            )
            task.base_commit = base
            task.architecture_worktree_path = str(worktree.worktree_path)
            task.current_role = "architect"
            await session.commit()

            role = self._roles.effective("architect")
            workspace = {
                "cwd": str(worktree.worktree_path),
                "repo_path": str(repo),
                "branch": None,
                "base_commit": base,
                "permissions": self.permission_names(role),
            }
            prompt = await self._prompts.build(
                role=role, project=project, task=task, workspace=workspace
            )
            execution = await self.create_execution(
                task=task,
                project=project,
                role=role,
                workspace=workspace,
                system_prompt=prompt.system,
                user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()
        await self.engine.start(execution.id)
        return execution

    # --------------------------------------------------- architecture gates

    async def approve_architecture(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "approve_architecture", "founder", session)
        await self._cleanup_architect_worktree(task_id)

    async def reject_architecture(self, task_id: int, reason: str = "") -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "reject_architecture", "founder", session)
        await self._cleanup_architect_worktree(task_id)
        if reason:
            await self._append_task_note(task_id, "architecture rejected", reason)

    async def request_architecture_revision(self, task_id: int, notes: str = "") -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(
                task, "request_architecture_revision", "founder", session
            )
        await self._cleanup_architect_worktree(task_id)
        if notes:
            await self._append_task_note(task_id, "architecture revision requested", notes)

    async def _cleanup_architect_worktree(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            path = task.architecture_worktree_path
            repo = (await self._get_project(session, task.project_id)).repository_path
            task.architecture_worktree_path = None
            await session.commit()
        if path:
            try:
                await self._git.remove_worktree(Path(repo), Path(path), None)
            except Exception:  # noqa: BLE001 - cleanup must never break the flow
                logger.warning("architect worktree cleanup failed for task %s", task_id)

    # ------------------------------------------------------- task: Engineer

    async def start_implementation(self, task_id: int) -> Execution:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)
            await self._transition(task, "start_implementation", "founder", session)

            repo = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(f"repository at {repo} is not a valid Git repository", 400)
            branch = f"sw-task-{task.id}"
            base = task.base_commit or await self._git.resolve_base_commit(
                repo, project.default_branch
            )
            task.task_branch = branch
            if task.worktree_path and Path(task.worktree_path).is_dir():
                worktree_path = Path(task.worktree_path)
            else:
                worktree = await self._git.create_branch_worktree(
                    repo, base, task.id, branch
                )
                worktree_path = worktree.worktree_path
                task.worktree_path = str(worktree_path)
            task.current_role = "engineer"
            await session.commit()

            role = self._roles.effective("engineer")
            workspace = {
                "cwd": str(worktree_path),
                "repo_path": str(repo),
                "branch": branch,
                "base_commit": base,
                "permissions": self.permission_names(role),
            }
            prompt = await self._prompts.build(
                role=role, project=project, task=task, workspace=workspace
            )
            execution = await self.create_execution(
                task=task,
                project=project,
                role=role,
                workspace=workspace,
                system_prompt=prompt.system,
                user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()
        await self.engine.start(execution.id)
        return execution

    # -------------------------------------------------------- task: Reviewer

    async def start_review(self, task_id: int) -> Execution:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)
            await self._transition(task, "start_review", "founder", session)
            repo = Path(project.repository_path).resolve()
            if not task.worktree_path or not task.result_commit:
                raise WorkflowError(
                    "cannot review: no implementation commit found", 409
                )
            review_worktree = await self._git.create_detached_worktree(
                repo, task.result_commit, task.id, suffix="-review"
            )
            task.review_worktree_path = str(review_worktree.worktree_path)
            task.current_role = "reviewer"
            await session.commit()

            role = self._roles.effective("reviewer")
            workspace = {
                "cwd": str(review_worktree.worktree_path),
                "repo_path": str(repo),
                "branch": None,
                "base_commit": task.base_commit,
                "permissions": self.permission_names(role),
            }
            diff = await self._git.diff(Path(task.worktree_path), task.base_commit or task.result_commit)
            commits = await self._git.list_commits(
                Path(task.worktree_path), task.base_commit or task.result_commit
            )
            extra = {
                "Diff to review (base_commit..result_commit)": _cap(
                    diff["full"], 120_000
                ),
                "Commits": "\n".join(
                    f"- {c['sha']} {c['subject']}" for c in commits
                ),
            }
            prompt = await self._prompts.build(
                role=role, project=project, task=task, workspace=workspace, extra=extra
            )
            execution = await self.create_execution(
                task=task,
                project=project,
                role=role,
                workspace=workspace,
                system_prompt=prompt.system,
                user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()
        await self.engine.start(execution.id)
        return execution

    # ------------------------------------------------------- human gates

    async def accept(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "accept", "founder", session)

    async def reject(self, task_id: int, reason: str = "") -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "reject", "founder", session)
        if reason:
            await self._append_task_note(task_id, "work rejected", reason)

    async def send_back_to_engineer(self, task_id: int, notes: str = "") -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "send_back_to_engineer", "founder", session)
        if notes:
            await self._append_task_note(task_id, "sent back to engineer", notes)

    # ------------------------------------------------------------ lifecycle

    async def cancel(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            execution_id = task.current_execution_id
            await self._transition(task, "cancel", "founder", session)
        if execution_id:
            await self.engine.cancel(execution_id)

    async def retry(self, task_id: int) -> Execution | None:
        """Re-run the failed phase. Implementation retry by default; architecture
        re-analysis when no architecture result was ever produced."""
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status != TaskStatus.FAILED.value:
                raise WorkflowError("retry is only available for FAILED tasks", 409)
        if task.architecture_result:
            return await self.start_implementation(task_id)
        return await self.start_architecture(task_id)

    async def cleanup_worktree(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status in RUNNING:
                raise WorkflowError("cannot clean up while the task is running", 409)
            repo = (await self._get_project(session, task.project_id)).repository_path
            worktree_path = task.worktree_path
            branch = task.task_branch
            task.worktree_path = None
            task.task_branch = None
            await session.commit()
        if worktree_path:
            await self._git.remove_worktree(Path(repo), Path(worktree_path), branch)
        await self._append_task_note(task_id, "worktree cleaned up", "")

    async def _append_task_note(self, task_id: int, title: str, detail: str) -> None:
        await self._events.append(
            execution_id=None,
            task_id=task_id,
            type="task.note",
            payload={"title": title, "detail": detail[:4000]},
        )

    # ------------------------------------------------------- continuation

    async def on_execution_finished(self, execution_id: str) -> None:
        """Called by the engine when any execution terminates."""
        async with self._session_factory() as session:
            execution = await session.get(Execution, execution_id)
            if execution is None:
                return
            task = None
            if execution.task_id is not None:
                task = await self._get_task(session, execution.task_id)

            if task is None:
                if execution.role in COMPANY_ROLES or execution.role == "architect":
                    await self._store_company_artifact(session, execution)
                return

            if execution.role == "architect":
                await self._finish_architect(session, task, execution)
            elif execution.role == "engineer":
                await self._finish_engineer(session, task, execution)
            elif execution.role == "reviewer":
                await self._finish_reviewer(session, task, execution)
            else:
                logger.warning("execution %s has unexpected role %s", execution.id, execution.role)
            session.add(task)

    async def _finish_architect(
        self, session: AsyncSession, task: Task, execution: Execution
    ) -> None:
        task.current_role = None
        task.current_execution_id = None
        if execution.status == "COMPLETED":
            task.architecture_result = execution.result or "(architect produced no analysis)"
            await self._transition(task, "architecture_completed", "system", session)
        elif execution.status == "FAILED":
            # Task state was already cancelled for user-initiated cancellation;
            # only transition when the task is still in the analysis phase.
            if task.status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
                await self._transition(task, "architecture_failed", "system", session)

    async def _finish_engineer(
        self, session: AsyncSession, task: Task, execution: Execution
    ) -> None:
        task.current_role = None
        task.current_execution_id = None
        if execution.status == "COMPLETED":
            commit = None
            if task.worktree_path:
                try:
                    commit = await self._git.head_commit(Path(task.worktree_path))
                except Exception:  # noqa: BLE001
                    logger.warning("could not read engineer commit for task %s", task.id)
            task.result_commit = commit or execution.workspace.get("result_commit")
            task.implementation_summary = execution.result or ""
            await self._transition(task, "implementation_completed", "system", session)
            if commit:
                await self._events.append(
                    execution_id=execution.id,
                    task_id=task.id,
                    type=event_types.GIT_COMMIT,
                    payload={"commit": commit, "message": "Engineer implementation commit"},
                )
        elif execution.status == "FAILED":
            if task.status == TaskStatus.IMPLEMENTING.value:
                await self._transition(task, "implementation_failed", "system", session)

    async def _finish_reviewer(
        self, session: AsyncSession, task: Task, execution: Execution
    ) -> None:
        task.current_role = None
        task.current_execution_id = None
        if execution.status == "COMPLETED":
            task.review_result = execution.result or ""
            verdict = parse_review_verdict(execution.result or "")
            action = (
                "review_completed" if verdict == "APPROVED" else "review_changes_requested"
            )
            await self._transition(task, action, "system", session)
            await self._cleanup_review_worktree(task)
        elif execution.status == "FAILED":
            if task.status == TaskStatus.REVIEWING.value:
                await self._transition(task, "review_failed", "system", session)

    async def _cleanup_review_worktree(self, task: Task) -> None:
        # The reviewer worktree is disposable; the engineer worktree stays.
        path = task.review_worktree_path
        if not path:
            return
        async with self._session_factory() as session:
            project = await self._get_project(session, task.project_id)
            try:
                await self._git.remove_worktree(Path(project.repository_path), Path(path), None)
            except Exception:  # noqa: BLE001
                logger.warning("review worktree cleanup failed for task %s", task.id)
            task_row = await session.get(Task, task.id)
            task_row.review_worktree_path = None
            await session.commit()

    # ------------------------------------------------------ company artifacts

    async def _store_company_artifact(
        self, session: AsyncSession, execution: Execution
    ) -> None:
        workspace = execution.workspace or {}
        title = workspace.get("ask_title") or f"{execution.role} decision"
        content = execution.result or f"Execution failed: {execution.error or 'unknown'}"
        artifact = Artifact(
            kind="company_decision",
            role=execution.role,
            project_id=workspace.get("project_id") if isinstance(workspace.get("project_id"), int) else None,
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
            payload={"artifact_id": artifact.id, "role": execution.role},
        )


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


