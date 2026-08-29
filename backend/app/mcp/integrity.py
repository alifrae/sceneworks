"""Control-plane integrity overrides for the canonical semantic MCP surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.domain.task_states import TaskStateMachine, TaskStatus
from app.mcp.server import (
    MCPToolError,
    _event_row,
    _execution_summary,
    _int_arg,
    _memory_row,
    _task_summary,
)
from app.mcp.wp18_server import GuiAutomationMCPServer
from app.models import Event, Execution, Project, Task


def _diagnostic(prefix: str, exc: BaseException) -> dict[str, Any]:
    message = str(exc).strip()
    return {
        "code": prefix,
        "exception_type": type(exc).__name__,
        "detail": (message or type(exc).__name__)[:1000],
    }


class ControlPlaneIntegrityMCPServer(GuiAutomationMCPServer):
    """Make semantic reads durable and explicit about unavailable evidence."""

    async def _get_project_context(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = _int_arg(args, "project_id")
        task_limit = min(max(int(args.get("task_limit", 10)), 1), 50)
        query = str(args.get("query") or "").strip()
        async with self.ctx.engine_factory() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise MCPToolError(f"project {project_id} not found")
            tasks = (
                await session.execute(
                    select(Task)
                    .where(Task.project_id == project_id)
                    .order_by(Task.updated_at.desc())
                    .limit(task_limit)
                )
            ).scalars().all()

        try:
            memories = await self.ctx.memory.search(
                project_id, query=query, status="accepted", limit=10
            )
            memory_availability: dict[str, Any] = {"state": "available"}
        except Exception as exc:  # noqa: BLE001 - context remains partially answerable
            memories = []
            memory_availability = {
                "state": "unavailable",
                "diagnostic": _diagnostic("accepted_memory_unavailable", exc),
            }

        persisted_root = Path(project.repository_path)
        try:
            repo_snapshot = await self.ctx.git.repository_snapshot(
                persisted_root, project.default_branch
            )
        except Exception as exc:  # noqa: BLE001 - never fabricate an empty snapshot
            repo_snapshot = {
                "is_git": False,
                "current_branch": None,
                "default_branch": project.default_branch,
                "head_commit": None,
                "clean": None,
                "dirty": None,
                "availability": {"state": "unavailable"},
                "diagnostic": _diagnostic("git_probe_failed", exc),
                "error": f"git_probe_failed: {type(exc).__name__}: {str(exc).strip()}".rstrip(": "),
            }

        return {
            "project": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "status": project.status,
                "default_branch": project.default_branch,
                "architecture_context_paths": list(
                    project.architecture_context_paths or []
                ),
                "test_commands": list(project.test_commands or []),
                "build_commands": list(project.build_commands or []),
                "engineering_policy": project.engineering_policy or {},
                "capability_profile": project.capability_profile or {},
            },
            "repository_snapshot": repo_snapshot,
            "recent_tasks": [_task_summary(task) for task in tasks],
            "accepted_memory": [_memory_row(memory) for memory in memories],
            "accepted_memory_availability": memory_availability,
            "authority_note": (
                "Only accepted project memory is authoritative; agent outputs are "
                "execution evidence/inference until reviewed."
            ),
        }

    async def _get_task(self, args: dict[str, Any]) -> dict[str, Any]:
        task_id = _int_arg(args, "task_id")
        event_limit = min(max(int(args.get("event_limit", 40)), 0), 200)
        execution_limit = min(max(int(args.get("execution_limit", 10)), 0), 50)
        async with self.ctx.engine_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise MCPToolError(f"task {task_id} not found")
            project = await session.get(Project, task.project_id)
            executions: list[Execution] = []
            if execution_limit:
                executions = list(
                    (
                        await session.execute(
                            select(Execution)
                            .where(Execution.task_id == task_id)
                            .order_by(Execution.created_at.desc())
                            .limit(execution_limit)
                        )
                    ).scalars().all()
                )
            events: list[Event] = []
            if event_limit:
                events = list(
                    (
                        await session.execute(
                            select(Event)
                            .where(Event.task_id == task_id)
                            .order_by(Event.id.desc())
                            .limit(event_limit)
                        )
                    ).scalars().all()
                )

        try:
            allowed = TaskStateMachine.allowed_actions(TaskStatus(task.status))
        except ValueError:
            allowed = []

        try:
            provenance = await self.ctx.provenance.task(task_id)
            provenance_availability: dict[str, Any] = {"state": "available"}
        except Exception as exc:  # noqa: BLE001 - task truth survives missing live Git evidence
            provenance = {
                "task_id": task.id,
                "project_id": task.project_id,
                "title": task.title,
                "status": task.status,
                "base_commit": task.base_commit,
                "result_commit": task.result_commit,
                "task_branch": task.task_branch,
                "changed_files": list(task.changed_files or []),
                "source_memory_ids": [],
            }
            provenance_availability = {
                "state": "degraded",
                "diagnostic": _diagnostic("live_provenance_unavailable", exc),
            }

        return {
            "task": {
                **_task_summary(task),
                "project_name": project.name if project else None,
                "description": task.description,
                "engineering_contract": task.engineering_contract or {},
                "capability_requirements": task.capability_requirements or {},
                "advisory_results": task.advisory_results or {},
                "architecture_result": task.architecture_result,
                "implementation_summary": task.implementation_summary,
                "review_result": task.review_result,
                "task_branch": task.task_branch,
                "changed_files": list(task.changed_files or []),
                "allowed_actions": allowed,
            },
            "provenance": provenance,
            "provenance_availability": provenance_availability,
            "executions": [_execution_summary(row) for row in executions],
            "events": [_event_row(event) for event in reversed(events)],
        }

    async def _get_task_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        task_id = _int_arg(args, "task_id")
        async with self.ctx.engine_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise MCPToolError(f"task {task_id} not found")
            project = await session.get(Project, task.project_id)

        base = task.base_commit
        result_commit = task.result_commit
        changed_files = list(task.changed_files or [])
        diagnostics: list[dict[str, Any]] = []

        if task.worktree_path and Path(task.worktree_path).is_dir() and base:
            worktree = Path(task.worktree_path)
            try:
                diff = await self.ctx.git.diff(worktree, base)
                commits = await self.ctx.git.list_commits(worktree, base)
                status = await self.ctx.git.status(worktree)
                observed_files = await self.ctx.git.changed_files(worktree, base)
                return {
                    "task_id": task_id,
                    "available": True,
                    "source": "live_worktree",
                    "availability": {"state": "available"},
                    "base_commit": base,
                    "result_commit": result_commit,
                    "changed_files": observed_files or changed_files,
                    "stat": diff.get("stat", ""),
                    "diff": diff.get("full", ""),
                    "commits": commits,
                    "status": status,
                    "diagnostics": [],
                }
            except Exception as exc:  # noqa: BLE001 - immutable fallback may still answer
                diagnostics.append(_diagnostic("live_worktree_unavailable", exc))

        if project is not None and base and result_commit:
            try:
                immutable = await self.ctx.git.diff_between(
                    Path(project.repository_path), base, result_commit
                )
                return {
                    "task_id": task_id,
                    "available": True,
                    "source": "immutable_commits",
                    "availability": {"state": "available"},
                    "base_commit": base,
                    "result_commit": result_commit,
                    "changed_files": immutable.get("changed_files") or changed_files,
                    "stat": immutable.get("stat", ""),
                    "diff": immutable.get("full", ""),
                    "commits": immutable.get("commits", []),
                    "status": None,
                    "diagnostics": diagnostics,
                }
            except Exception as exc:  # noqa: BLE001 - persisted provenance is final fallback
                diagnostics.append(_diagnostic("immutable_diff_unavailable", exc))

        if changed_files:
            return {
                "task_id": task_id,
                "available": False,
                "source": "persisted_provenance",
                "availability": {
                    "state": "partial",
                    "reason": "diff_content_unavailable",
                },
                "base_commit": base,
                "result_commit": result_commit,
                "changed_files": changed_files,
                "stat": "",
                "diff": "",
                "commits": [],
                "status": None,
                "diagnostics": diagnostics,
            }

        if not base:
            reason = "base_commit_missing"
        elif not result_commit:
            reason = "result_commit_missing"
        elif project is None:
            reason = "project_unavailable"
        else:
            reason = "diff_unavailable"
        return {
            "task_id": task_id,
            "available": False,
            "source": None,
            "availability": {"state": "unavailable", "reason": reason},
            "base_commit": base,
            "result_commit": result_commit,
            "changed_files": changed_files,
            "stat": "",
            "diff": "",
            "commits": [],
            "status": None,
            "diagnostics": diagnostics,
        }

    async def _pcs_get_config(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = _int_arg(args, "project_id")
        configured, config = await self.ctx.pcs_control.get_config_state(project_id)
        if not configured:
            return {
                "project_id": project_id,
                "availability": {
                    "state": "not_configured",
                    "reason": "no persisted PCS runtime configuration",
                },
                "config": None,
            }
        return {
            "project_id": project_id,
            "availability": {"state": "available"},
            "config": self.ctx.pcs_control.public_config(config),
        }
