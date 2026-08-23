"""Triage and advisory-role execution mechanics outside LangGraph nodes (WP7)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.task_states import TaskStatus
from app.events import types as event_types
from app.execution.engine import ExecutionEngine
from app.git.workspace import GitError, GitWorktreeService
from app.roles.definitions import RoleDefinition
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry
from app.services.workflow import WorkflowError
from app.workflows.formatting import format_memory_block
from app.workflows.role_runtime import WorkflowRoleRuntime
from app.workflows.runtime import WorkflowRuntime
from app.workflows.state import InitiativeState

logger = logging.getLogger("sceneworks.workflows.advisory")

TRIAGE_MODEL_PROFILE = "strongest"


class WorkflowAdvisoryRuntime:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: ExecutionEngine,
        git: GitWorktreeService,
        prompts: PromptBuilder,
        roles: RoleRegistry,
        runtime: WorkflowRuntime,
        role_runtime: WorkflowRoleRuntime,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._git = git
        self._prompts = prompts
        self._roles = roles
        self._runtime = runtime
        self._role_runtime = role_runtime

    async def run_triage(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        logger.info("triage node starting for task %s", task_id)

        async with self._session_factory() as session:
            task = await self._runtime.get_task(session, task_id)
            project = await self._runtime.get_project(session, task.project_id)

        role_backend = self._roles.effective("architect").backend
        system, user = PromptBuilder.build_triage_prompt(task, project)

        memory_ctx = await self._runtime.inject_memory(
            project.id,
            task.description or task.title,
        )
        if memory_ctx.get("memories"):
            user = user + "\n\n" + format_memory_block(memory_ctx["memories"])
            await self._runtime.emit_memory_injection(task_id, "triage", memory_ctx)

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
        worktree = await self._git.create_detached_worktree(
            repo,
            base,
            task.id,
            suffix="-triage",
        )
        workspace = {
            "cwd": str(worktree.worktree_path),
            "repo_path": str(repo),
            "branch": None,
            "base_commit": base,
            "permissions": ["repository_read", "network_access"],
        }
        role = RoleDefinition(
            key="triage",
            display_name="Triage",
            description="classifies request type and selects participants",
            backend=role_backend,
            model_profile=TRIAGE_MODEL_PROFILE,
        )

        try:
            async with self._session_factory() as session:
                current_task = await self._runtime.get_task(session, task_id)
                current_task.base_commit = base
                execution = await self._runtime.create_execution(
                    task=current_task,
                    role=role,
                    workspace=workspace,
                    system_prompt=system,
                    user_prompt=user,
                )
                current_task.current_execution_id = execution.id
                current_task.current_role = "triage"
                await session.commit()

            await self._runtime.emit_workflow_event(
                task_id,
                "workflow.node.started",
                {
                    "node": "triage",
                    "execution_id": execution.id,
                    "base_commit": base,
                },
            )
            await self._engine.start(execution.id)
            final = await self._runtime.wait_for_execution(execution.id)
            triage_data = PromptBuilder.parse_triage_result(final.result or "")

            if triage_data.pop("triage_parse_failed", False):
                logger.warning(
                    "triage output for task %s could not be parsed; default routing",
                    task_id,
                )
                triage_data["triage_degraded"] = True
                await self._runtime.emit_workflow_event(
                    task_id,
                    "workflow.triage.degraded",
                    {
                        "execution_id": final.id,
                        "status": final.status,
                        "reason": "output could not be parsed as JSON",
                    },
                )
                await self._runtime.append_task_note(
                    task_id,
                    "triage output could not be parsed",
                    triage_data["reasoning_summary"],
                )

            if final.status != "COMPLETED":
                logger.warning(
                    "triage execution for task %s ended %s; using default routing",
                    task_id,
                    final.status,
                )
                triage_data["reasoning_summary"] = (
                    f"triage execution {final.status.lower()} "
                    f"({final.error or 'no error recorded'}); "
                    "default routing applied — participant selection and "
                    "requires_implementation were NOT determined by triage"
                )
                triage_data["triage_degraded"] = True
                await self._runtime.emit_workflow_event(
                    task_id,
                    "workflow.triage.degraded",
                    {
                        "execution_id": final.id,
                        "status": final.status,
                        "error": final.error,
                    },
                )
                await self._runtime.append_task_note(
                    task_id,
                    "triage did not complete",
                    triage_data["reasoning_summary"],
                )

            if final.status == "CANCELLED":
                await self._runtime.set_task_status(
                    task_id,
                    TaskStatus.CANCELLED,
                    "founder",
                )
                return {
                    **state,
                    "task_status": TaskStatus.CANCELLED.value,
                    "triage_executed": True,
                    "error": "cancelled",
                }
        finally:
            try:
                await self._git.remove_worktree(repo, worktree.worktree_path, None)
            except Exception:  # noqa: BLE001
                logger.warning("triage worktree cleanup failed for task %s", task_id)

        await self._runtime.emit_workflow_event(
            task_id,
            event_types.WORKFLOW_TRIAGE_COMPLETED,
            {"result": triage_data},
        )
        for display, key, role_key in [
            ("Product", "use_product", "product"),
            ("CTO", "use_cto", "cto"),
            ("Technical Expert", "use_technical_expert", "technical_expert"),
        ]:
            await self._runtime.emit_workflow_event(
                task_id,
                (
                    event_types.WORKFLOW_ROLE_SELECTED
                    if triage_data.get(key)
                    else event_types.WORKFLOW_ROLE_SKIPPED
                ),
                {"role": role_key, "display": display},
            )

        return {
            **state,
            "triage_executed": True,
            "triage_result": json.dumps(triage_data),
            "request_type": triage_data.get("request_type", "feature"),
            "requires_implementation": triage_data.get("requires_implementation", True),
            "error": None,
        }

    async def run_advisory_role(
        self,
        state: InitiativeState,
        role_key: str,
        display: str,
    ) -> InitiativeState:
        task_id = state["task_id"]
        logger.info("%s node starting for task %s", role_key, task_id)

        repo: Path | None = None
        worktree = None
        try:
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
                worktree = await self._git.create_detached_worktree(
                    repo,
                    base,
                    task.id,
                    suffix=f"-{role_key}",
                )
                task.current_role = role_key

                role = self._roles.effective(role_key)
                workspace = {
                    "cwd": str(worktree.worktree_path),
                    "repo_path": str(repo),
                    "branch": None,
                    "base_commit": base,
                    "permissions": self._runtime.permission_names(role),
                }
                prompt = await self._prompts.build(
                    role=role,
                    project=project,
                    task=task,
                    workspace=workspace,
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

            await self._runtime.emit_workflow_event(
                task_id,
                "workflow.node.started",
                {"node": role_key, "execution_id": execution.id},
            )
            await self._engine.start(execution.id)
            final = await self._runtime.wait_for_execution(execution.id)

            if final.status == "COMPLETED":
                result_content = final.result or f"({display} produced no output)"
                await self._role_runtime.store_task_advisory_result(
                    task_id,
                    role_key,
                    result_content,
                )
                return {
                    **state,
                    f"{role_key}_executed": True,
                    f"{role_key}_execution_id": final.id,
                    f"{role_key}_result": result_content,
                    "error": None,
                }

            logger.warning(
                "%s execution for task %s: %s",
                role_key,
                task_id,
                final.status,
            )
            return {
                **state,
                f"{role_key}_executed": True,
                f"{role_key}_execution_id": final.id,
                f"{role_key}_result": f"({display} execution {final.status})",
                "error": None,
            }
        except (WorkflowError, GitError) as exc:
            logger.error(
                "%s preparation failed for task %s: %s",
                role_key,
                task_id,
                exc,
            )
            return {
                **state,
                f"{role_key}_executed": True,
                "error": str(exc),
            }
        finally:
            if worktree is not None and repo is not None:
                try:
                    await self._git.remove_worktree(
                        repo,
                        worktree.worktree_path,
                        None,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "failed to clean up %s worktree for task %s",
                        role_key,
                        task_id,
                    )
