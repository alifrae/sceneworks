"""Architect/Engineer/Reviewer execution lifecycle outside graph nodes (WP7).

Graph nodes decide *when* a role runs and how its result routes. This component
owns the operational mechanics of preparing worktrees/prompts/executions and
persisting role results. That keeps Git/worktree/persistence side effects out of
LangGraph node bodies.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.task_states import TaskStatus
from app.events import types as event_types
from app.events.store import EventStore
from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.models import Execution, Task
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry
from app.services.workflow import WorkflowError
from app.workflows.formatting import cap_text, format_memory_block
from app.workflows.runtime import WorkflowRuntime
from app.workflows.state import InitiativeState

logger = logging.getLogger("sceneworks.workflows.roles")


class WorkflowRoleRuntime:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: ExecutionEngine,
        git: GitWorktreeService,
        prompts: PromptBuilder,
        roles: RoleRegistry,
        event_store: EventStore,
        runtime: WorkflowRuntime,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._git = git
        self._prompts = prompts
        self._roles = roles
        self._events = event_store
        self._runtime = runtime

    # --------------------------------------------------------- preparation

    async def prepare_and_start_architect(
        self,
        task_id: int,
        state: InitiativeState | None = None,
    ) -> Execution:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            project = await self._runtime.get_project(session, task.project_id)

            repo = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(
                    f"repository at {repo} is not a valid Git repository",
                    400,
                )
            base = task.base_commit or await self._git.resolve_base_commit(
                repo,
                project.default_branch,
            )

            if task.architecture_worktree_path:
                try:
                    await self._git.remove_worktree(
                        repo,
                        Path(task.architecture_worktree_path),
                        None,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "stale architect worktree cleanup failed for task %s",
                        task_id,
                    )

            worktree = await self._git.create_detached_worktree(repo, base, task.id)
            task.base_commit = base
            task.architecture_worktree_path = str(worktree.worktree_path)
            task.current_role = "architect"

            role = self._roles.effective("architect")
            workspace = {
                "cwd": str(worktree.worktree_path),
                "repo_path": str(repo),
                "branch": None,
                "base_commit": base,
                "permissions": self._runtime.permission_names(role),
            }

            upstream: dict[str, str] = {}
            if state:
                for label, key in [
                    ("Product requirements", "product_result"),
                    ("CTO recommendation", "cto_result"),
                    ("Technical Expert assessment", "technical_expert_result"),
                ]:
                    value = state.get(key)
                    if value:
                        upstream[label] = value

            memory_ctx = await self._runtime.inject_memory(
                project.id,
                task.description or task.title,
                types=["architecture_decision", "technology_decision", "constraint"],
            )
            if memory_ctx.get("memories"):
                upstream["Accepted project decisions and constraints"] = (
                    format_memory_block(memory_ctx["memories"])
                )
                await self._runtime.emit_memory_injection(
                    task_id,
                    "architect",
                    memory_ctx,
                )

            prompt = await self._prompts.build(
                role=role,
                project=project,
                task=task,
                workspace=workspace,
                extra={"Execution intent": _architect_intent_instruction(task)},
                upstream_contexts=upstream or None,
                context_worktree_path=str(worktree.worktree_path),
            )
            execution = await self._runtime.create_execution(
                task=task,
                role=role,
                workspace=workspace,
                system_prompt=prompt.system,
                user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()

        await self._engine.start(execution.id)
        return execution

    async def prepare_and_start_engineer(
        self,
        task_id: int,
        is_correction: bool = False,
    ) -> Execution:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            project = await self._runtime.get_project(session, task.project_id)

            if task.status != TaskStatus.IMPLEMENTING.value:
                await self._runtime.transition(
                    task,
                    "start_implementation",
                    "system",
                    session,
                )

            repo = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(
                    f"repository at {repo} is not a valid Git repository",
                    400,
                )

            branch = f"sw-task-{task.id}"
            base = task.base_commit or await self._git.resolve_base_commit(
                repo,
                project.default_branch,
            )
            task.task_branch = branch
            if task.worktree_path and Path(task.worktree_path).is_dir():
                worktree_path = Path(task.worktree_path)
            else:
                worktree = await self._git.create_branch_worktree(
                    repo,
                    base,
                    task.id,
                    branch,
                )
                worktree_path = worktree.worktree_path
                task.worktree_path = str(worktree_path)
            task.current_role = "engineer"

            role = self._roles.effective("engineer")
            workspace = {
                "cwd": str(worktree_path),
                "repo_path": str(repo),
                "branch": branch,
                "base_commit": base,
                "permissions": self._runtime.permission_names(role),
            }

            upstream: dict[str, str] = {}
            if task.architecture_result:
                upstream["Approved architecture"] = task.architecture_result
            async with self._session_factory() as latest_session:
                latest_task = await self._runtime.get_task(latest_session, task_id)
                if latest_task.review_result and is_correction:
                    upstream["Reviewer corrections to address"] = latest_task.review_result

            prompt = await self._prompts.build(
                role=role,
                project=project,
                task=task,
                workspace=workspace,
                is_correction=is_correction,
                upstream_contexts=upstream or None,
                context_worktree_path=str(worktree_path),
            )
            execution = await self._runtime.create_execution(
                task=task,
                role=role,
                workspace=workspace,
                system_prompt=prompt.system,
                user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()

        await self._engine.start(execution.id)
        return execution

    async def prepare_and_start_reviewer(self, task_id: int) -> Execution:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            project = await self._runtime.get_project(session, task.project_id)

            if task.status != TaskStatus.REVIEWING.value:
                await self._runtime.transition(task, "start_review", "system", session)

            if not task.worktree_path or not task.result_commit:
                raise WorkflowError("cannot review: no implementation commit found", 409)

            repo = Path(project.repository_path).resolve()
            if task.review_worktree_path:
                previous = Path(task.review_worktree_path)
                try:
                    await self._git.remove_worktree(repo, previous, None)
                except Exception:  # noqa: BLE001
                    shutil.rmtree(str(previous), ignore_errors=True)
                task.review_worktree_path = None

            review_worktree = await self._git.create_detached_worktree(
                repo,
                task.result_commit,
                task.id,
                suffix="-review",
            )
            task.review_worktree_path = str(review_worktree.worktree_path)
            task.current_role = "reviewer"

            role = self._roles.effective("reviewer")
            workspace = {
                "cwd": str(review_worktree.worktree_path),
                "repo_path": str(repo),
                "branch": None,
                "base_commit": task.base_commit,
                "permissions": self._runtime.permission_names(role),
            }
            diff = await self._git.diff(
                Path(task.worktree_path),
                task.base_commit or task.result_commit,
            )
            commits = await self._git.list_commits(
                Path(task.worktree_path),
                task.base_commit or task.result_commit,
            )
            extra = {
                "Diff to review (base_commit..result_commit)": cap_text(
                    diff["full"],
                    120_000,
                ),
                "Commits": "\n".join(
                    f"- {commit['sha']} {commit['subject']}" for commit in commits
                ),
            }
            prompt = await self._prompts.build(
                role=role,
                project=project,
                task=task,
                workspace=workspace,
                extra=extra,
                context_worktree_path=str(review_worktree.worktree_path),
            )
            execution = await self._runtime.create_execution(
                task=task,
                role=role,
                workspace=workspace,
                system_prompt=prompt.system,
                user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()

        await self._engine.start(execution.id)
        return execution

    # ----------------------------------------------------------- finish hooks

    async def finish_architect(
        self,
        task_id: int,
        execution: Execution,
        requires_implementation: bool = True,
    ) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None
            task.architecture_result = execution.result or "(architect produced no analysis)"
            if task.status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
                action = (
                    "architecture_completed"
                    if requires_implementation
                    else "advisory_completed"
                )
                await self._runtime.transition(task, action, "system", session)
            else:
                await session.commit()

    async def finish_engineer(self, task_id: int, execution: Execution) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None
            commit = None
            committed_by_sceneworks = False
            if task.worktree_path:
                commit, committed_by_sceneworks = await self.capture_engineer_commit(
                    task_id,
                    Path(task.worktree_path),
                    task.base_commit,
                )
            task.result_commit = commit or execution.workspace.get("result_commit")
            task.implementation_summary = execution.result or ""
            if task.status == TaskStatus.IMPLEMENTING.value:
                await self._runtime.transition(
                    task,
                    "implementation_completed",
                    "system",
                    session,
                )
                if commit and commit != task.base_commit:
                    await self._events.append(
                        execution_id=execution.id,
                        task_id=task_id,
                        type=event_types.GIT_COMMIT,
                        payload={
                            "commit": commit,
                            "message": (
                                "Uncommitted engineer changes committed by SceneWorks"
                                if committed_by_sceneworks
                                else "Engineer implementation commit"
                            ),
                        },
                    )
                elif commit == task.base_commit:
                    await self._events.append(
                        execution_id=execution.id,
                        task_id=task_id,
                        type="task.note",
                        payload={
                            "title": "engineer produced no commit",
                            "detail": (
                                "The engineer execution finished without changing the "
                                "worktree. The reviewer will see an empty diff."
                            ),
                        },
                    )
            else:
                await session.commit()

    async def capture_engineer_commit(
        self,
        task_id: int,
        worktree: Path,
        base_commit: str | None,
    ) -> tuple[str | None, bool]:
        try:
            head = await self._git.head_commit(worktree)
        except Exception:  # noqa: BLE001
            logger.warning("could not read engineer commit for task %s", task_id)
            return None, False

        if base_commit is not None and head != base_commit:
            return head, False

        try:
            dirty = (await self._git.status(worktree)).strip()
        except Exception:  # noqa: BLE001
            return head, False
        if not dirty:
            return head, False

        logger.info(
            "task %s: engineer left uncommitted changes; committing on its behalf",
            task_id,
        )
        try:
            new_head = await self._git.commit_all(
                worktree,
                f"sw-task-{task_id}: commit uncommitted engineer changes",
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "could not commit leftover engineer changes for task %s",
                task_id,
            )
            return head, False
        return new_head, True

    async def finish_reviewer(
        self,
        task_id: int,
        execution: Execution,
        verdict: str,
    ) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None
            task.review_result = execution.result or ""
            action = (
                "review_completed"
                if verdict == "APPROVED"
                else "review_changes_requested"
            )
            if task.status == TaskStatus.REVIEWING.value:
                await self._runtime.transition(task, action, "system", session)
            else:
                await session.commit()
            await self.cleanup_review_worktree(task)

    async def store_task_advisory_result(
        self,
        task_id: int,
        role_key: str,
        content: str,
    ) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None

            advisory = dict(task.advisory_results or {})
            advisory[role_key] = content[:20_000]
            task.advisory_results = advisory

            prefix = task.architecture_result or ""
            if role_key == "product":
                heading = "Product Assessment"
            elif role_key == "cto":
                heading = "CTO Assessment"
            elif role_key == "technical_expert":
                heading = "Technical Expert Assessment"
            else:
                heading = role_key
            task.architecture_result = (
                prefix + f"\n\n## {heading}\n{content[:20_000]}"
            ).strip()
            await session.commit()

    # ----------------------------------------------------- worktree/approval

    async def cleanup_review_worktree(self, task: Task) -> None:
        path = task.review_worktree_path
        if not path:
            return
        async with self._session_factory() as session:
            project = await self._runtime.get_project(session, task.project_id)
            try:
                await self._git.remove_worktree(
                    Path(project.repository_path),
                    Path(path),
                    None,
                )
            except Exception:  # noqa: BLE001
                logger.warning("review worktree cleanup failed for task %s", task.id)
            task_row = await session.get(Task, task.id)
            if task_row is not None:
                task_row.review_worktree_path = None
            await session.commit()

    async def approve_architecture(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            if task.status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
                await self._runtime.transition(
                    task,
                    "approve_architecture",
                    "founder",
                    session,
                )
        await self.cleanup_architect_worktree(task_id)

    async def reject_architecture(self, task_id: int, reason: str) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            if task.status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
                await self._runtime.transition(
                    task,
                    "reject_architecture",
                    "founder",
                    session,
                )
        await self.cleanup_architect_worktree(task_id)
        if reason:
            await self._runtime.append_task_note(
                task_id,
                "architecture rejected",
                reason,
            )

    async def request_revision(self, task_id: int, notes: str) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            if task.status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
                await self._runtime.transition(
                    task,
                    "request_architecture_revision",
                    "founder",
                    session,
                )
        await self.cleanup_architect_worktree(task_id)
        if notes:
            await self._runtime.append_task_note(
                task_id,
                "architecture revision requested",
                notes,
            )

    async def cleanup_architect_worktree(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            path = task.architecture_worktree_path
            repo = (
                await self._runtime.get_project(session, task.project_id)
            ).repository_path
            task.architecture_worktree_path = None
            await session.commit()
        if path:
            try:
                await self._git.remove_worktree(Path(repo), Path(path), None)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "architect worktree cleanup failed for task %s",
                    task_id,
                )


def _architect_intent_instruction(task: Task) -> str:
    mode = task.resolved_mode or task.requested_mode or "auto"
    prefix = f"Work item type: {task.work_item_type}. Requested mode: {task.requested_mode}. Resolved mode: {mode}."
    if mode == "ask":
        return prefix + " Answer the user's question directly from repository evidence. Do not turn it into an implementation plan and do not modify source."
    if mode == "investigate":
        return prefix + " Perform a read-only investigation. Focus on evidence, root cause or hypotheses, uncertainty, and recommended next steps. Do not implement changes."
    if mode == "plan":
        return prefix + " Produce a concrete architecture/design/implementation plan only. Do not modify source or behave as if implementation has been requested."
    return prefix + " Produce architecture guidance appropriate for a governed code change."
