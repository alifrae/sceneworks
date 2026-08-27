"""Stateless MCP HTTP server and semantic SceneWorks tool surface (WP11).

SceneWorks exposes engineering concepts, not raw machine primitives. In Observe
mode an external reasoning client can only inspect project/task/evidence state.
Standard mode additionally permits governed SceneWorks actions. Advanced mode
adds persistent Gemini ACP sessions in which the external MCP client supervises
Gemini iteratively while SceneWorks retains isolated worktrees, permission
mediation and Git provenance.

Raw file, shell, SQL and Git commands intentionally never become MCP tools.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app import __version__
from app.context import AppContext
from app.domain.task_states import TaskStateMachine, TaskStatus
from app.models import (
    Artifact,
    Event,
    Execution,
    Initiative,
    Project,
    ProjectMemory,
    Task,
    WorkPackage,
)
from app.schemas import CapabilityProfile, EngineeringContract, TaskCreate
from app.services.agent_sessions import AgentSessionError, session_row
from app.services.workflow import ASK_ALLOWED_ROLES, WorkflowError

LATEST_PROTOCOL = "2026-07-28"
LEGACY_PROTOCOL = "2025-11-25"
SUPPORTED_PROTOCOLS = (LATEST_PROTOCOL, LEGACY_PROTOCOL, "2025-06-18")
SERVER_NAME = "SceneWorks"


class MCPToolError(Exception):
    """Expected tool-domain error returned to the model as an MCP tool error."""


class SceneWorksMCPServer:
    """One stateless MCP request handler backed by a live :class:`AppContext`."""

    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    @property
    def mode(self) -> str:
        return self.ctx.settings.effective_mcp_mode

    # ---------------------------------------------------------------- tools

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Return only the tool surface allowed by the configured MCP mode."""
        read_only = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        }
        action = {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        }
        destructive = {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        }

        tools = [
            _tool(
                "sceneworks.capabilities",
                "Describe SceneWorks MCP mode, workflow roles, Gemini ACP capabilities and Advanced-session policy.",
                {},
                read_only,
            ),
            _tool(
                "sceneworks.list_projects",
                "List SceneWorks projects and their current activity.",
                {
                    "status": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    },
                },
                read_only,
            ),
            _tool(
                "sceneworks.get_project_context",
                "Get high-signal project context: policy, verification commands, recent tasks, accepted memory and repository snapshot.",
                {
                    "project_id": {"type": "integer", "minimum": 1},
                    "query": {
                        "type": "string",
                        "description": "Optional focus used to rank accepted project memory.",
                    },
                    "task_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    },
                },
                read_only,
                required=["project_id"],
            ),
            _tool(
                "sceneworks.list_tasks",
                "List tasks, optionally filtered by project, status or current role.",
                {
                    "project_id": {"type": "integer", "minimum": 1},
                    "status": {"type": "string"},
                    "role": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    },
                },
                read_only,
            ),
            _tool(
                "sceneworks.get_task",
                "Get a task with contract, architecture, implementation/review results, execution evidence, provenance and allowed actions.",
                {
                    "task_id": {"type": "integer", "minimum": 1},
                    "event_limit": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 200,
                        "default": 40,
                    },
                    "execution_limit": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 50,
                        "default": 10,
                    },
                },
                read_only,
                required=["task_id"],
            ),
            _tool(
                "sceneworks.get_task_diff",
                "Get implementation diff, commits and status for a task while its worktree is available.",
                {"task_id": {"type": "integer", "minimum": 1}},
                read_only,
                required=["task_id"],
            ),
            _tool(
                "sceneworks.get_execution",
                "Get one governed agent execution, result/error, snapshot metadata and recent events.",
                {
                    "execution_id": {"type": "string", "minLength": 1},
                    "event_limit": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 200,
                        "default": 80,
                    },
                },
                read_only,
                required=["execution_id"],
            ),
            _tool(
                "sceneworks.search_memory",
                "Search project memory. Accepted memory is authoritative; proposed memory is never silently promoted.",
                {
                    "project_id": {"type": "integer", "minimum": 1},
                    "query": {"type": "string", "default": ""},
                    "status": {
                        "type": "string",
                        "enum": [
                            "accepted",
                            "proposed",
                            "rejected",
                            "archived",
                            "superseded",
                        ],
                        "description": "Defaults to accepted for reasoning safety.",
                    },
                    "types": {"type": "array", "items": {"type": "string"}},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
                read_only,
                required=["project_id"],
            ),
            _tool(
                "sceneworks.list_artifacts",
                "List stored SceneWorks artifacts/role outputs for a project.",
                {
                    "project_id": {"type": "integer", "minimum": 1},
                    "role": {"type": "string"},
                    "kind": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
                read_only,
            ),
        ]

        if self.mode in {"standard", "advanced"}:
            tools.extend(
                [
                    _tool(
                        "sceneworks.inspect_repository",
                        "Use Gemini CLI as read-only engineering eyes on a commit-pinned snapshot. Starts a Technical Expert execution; poll get_execution.",
                        {
                            "project_id": {"type": "integer", "minimum": 1},
                            "question": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 20000,
                            },
                        },
                        action,
                        required=["project_id", "question"],
                    ),
                    _tool(
                        "sceneworks.ask_role",
                        "Ask a SceneWorks advisory role on a commit-pinned project snapshot and return the execution id.",
                        {
                            "role": {
                                "type": "string",
                                "enum": sorted(ASK_ALLOWED_ROLES),
                            },
                            "project_id": {"type": "integer", "minimum": 1},
                            "question": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 20000,
                            },
                        },
                        action,
                        required=["role", "question"],
                    ),
                    _tool(
                        "sceneworks.create_task",
                        "Create a structured SceneWorks engineering task. Does not start work.",
                        {
                            "project_id": {"type": "integer", "minimum": 1},
                            "work_package_id": {"type": "integer", "minimum": 1},
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 300,
                            },
                            "description": {"type": "string", "default": ""},
                            "priority": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                                "default": "medium",
                            },
                            "engineering_contract": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                            "capability_requirements": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                        },
                        action,
                        required=["project_id", "title"],
                    ),
                    _tool(
                        "sceneworks.task_action",
                        "Advance or control a task through the same governed workflow actions as the dashboard.",
                        {
                            "task_id": {"type": "integer", "minimum": 1},
                            "action": {
                                "type": "string",
                                "enum": [
                                    "start-architecture",
                                    "approve-architecture",
                                    "reject-architecture",
                                    "request-architecture-revision",
                                    "start-implementation",
                                    "start-review",
                                    "accept",
                                    "reject",
                                    "send-back",
                                    "cancel",
                                    "retry",
                                    "cleanup-worktree",
                                ],
                            },
                            "reason": {"type": "string", "default": ""},
                            "notes": {"type": "string", "default": ""},
                        },
                        destructive,
                        required=["task_id", "action"],
                    ),
                ]
            )

        if self.mode == "advanced":
            permission_schema = {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "repository_read",
                        "repository_write",
                        "shell_execute",
                        "git_commit",
                        "network_access",
                        "subagents",
                    ],
                },
                "description": "Optional subset of the server Advanced-session allowlist.",
            }
            tools.extend(
                [
                    _tool(
                        "sceneworks.agent_session.list",
                        "List persistent ChatGPT-supervised Gemini sessions.",
                        {
                            "project_id": {"type": "integer", "minimum": 1},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 200,
                                "default": 50,
                            },
                        },
                        read_only,
                    ),
                    _tool(
                        "sceneworks.agent_session.get",
                        "Get Advanced-session status, provider capabilities and latest Gemini result. No host paths are returned.",
                        {"session_id": {"type": "integer", "minimum": 1}},
                        read_only,
                        required=["session_id"],
                    ),
                    _tool(
                        "sceneworks.agent_session.diff",
                        "Inspect Git diff, commits and status in an Advanced session worktree.",
                        {"session_id": {"type": "integer", "minimum": 1}},
                        read_only,
                        required=["session_id"],
                    ),
                    _tool(
                        "sceneworks.agent_session.create",
                        "Create a persistent Gemini ACP execution subagent on an isolated project snapshot/worktree. Shell remains OS-user authority, not a full OS sandbox.",
                        {
                            "project_id": {"type": "integer", "minimum": 1},
                            "permissions": permission_schema,
                            "model": {
                                "type": "string",
                                "description": "Optional Gemini model for this session; defaults to SceneWorks Gemini model configuration.",
                            },
                        },
                        action,
                        required=["project_id"],
                    ),
                    _tool(
                        "sceneworks.agent_session.prompt",
                        "Send the next supervisor instruction to the persistent Gemini session. The turn runs asynchronously; poll agent_session.get.",
                        {
                            "session_id": {"type": "integer", "minimum": 1},
                            "prompt": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 30000,
                            },
                        },
                        action,
                        required=["session_id", "prompt"],
                    ),
                    _tool(
                        "sceneworks.agent_session.cancel",
                        "Cancel the currently running Advanced-session turn without deleting its worktree/provider conversation.",
                        {"session_id": {"type": "integer", "minimum": 1}},
                        destructive,
                        required=["session_id"],
                    ),
                    _tool(
                        "sceneworks.agent_session.close",
                        "Close an Advanced session. Optional cleanup removes only a clean worktree; the task branch/commits are preserved.",
                        {
                            "session_id": {"type": "integer", "minimum": 1},
                            "cleanup_worktree": {"type": "boolean", "default": False},
                        },
                        destructive,
                        required=["session_id"],
                    ),
                ]
            )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        args = arguments or {}
        handlers = {
            "sceneworks.capabilities": self._capabilities,
            "sceneworks.list_projects": self._list_projects,
            "sceneworks.get_project_context": self._get_project_context,
            "sceneworks.list_tasks": self._list_tasks,
            "sceneworks.get_task": self._get_task,
            "sceneworks.get_task_diff": self._get_task_diff,
            "sceneworks.get_execution": self._get_execution,
            "sceneworks.search_memory": self._search_memory,
            "sceneworks.list_artifacts": self._list_artifacts,
            "sceneworks.inspect_repository": self._inspect_repository,
            "sceneworks.ask_role": self._ask_role,
            "sceneworks.create_task": self._create_task,
            "sceneworks.task_action": self._task_action,
            "sceneworks.agent_session.list": self._agent_session_list,
            "sceneworks.agent_session.get": self._agent_session_get,
            "sceneworks.agent_session.diff": self._agent_session_diff,
            "sceneworks.agent_session.create": self._agent_session_create,
            "sceneworks.agent_session.prompt": self._agent_session_prompt,
            "sceneworks.agent_session.cancel": self._agent_session_cancel,
            "sceneworks.agent_session.close": self._agent_session_close,
        }
        handler = handlers.get(name)
        if handler is None:
            raise MCPToolError(f"unknown tool: {name}")
        try:
            result = await handler(args)
        except MCPToolError:
            raise
        except (WorkflowError, AgentSessionError) as exc:
            raise MCPToolError(str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise MCPToolError(str(exc)) from exc
        return _bounded(result, int(self.ctx.settings.mcp_tool_max_chars))

    async def _capabilities(self, args: dict[str, Any]) -> dict[str, Any]:
        del args
        roles = []
        for role in self.ctx.roles.all():
            roles.append(
                {
                    "role": role.key,
                    "backend": role.backend,
                    "model_profile": role.model_profile,
                    "permissions": sorted(p.value for p in role.permissions),
                    "can_modify_source": role.can_modify_source,
                    "can_commit": role.can_commit,
                }
            )
        mode = self.mode
        return {
            "server": {"name": SERVER_NAME, "version": __version__},
            "mcp": {
                "protocols": list(SUPPORTED_PROTOCOLS),
                "mode": mode,
                "action_tools_enabled": mode in {"standard", "advanced"},
                "advanced_agent_sessions_enabled": mode == "advanced",
                "boundary": (
                    "MCP exposes SceneWorks semantics only. Raw file, shell, Git "
                    "and SQL operations are intentionally not MCP tools."
                ),
            },
            "execution_model": {
                "reasoning_client": "external MCP client (for example ChatGPT)",
                "control_plane": "SceneWorks",
                "default_worker": self.ctx.settings.default_backend,
                "eyes": "inspect_repository -> Technical Expert on a detached commit-pinned worktree",
                "hands": "governed task workflow -> Engineer on an isolated branch worktree",
                "advanced": (
                    "ChatGPT supervises a persistent Gemini ACP conversation; "
                    "SceneWorks reloads it in the same isolated worktree for each turn"
                ),
                "verification": "Reviewer plus explicit human acceptance remain available",
            },
            "gemini_acp": {
                "file_proxy": "SceneWorks-mediated and worktree-confined",
                "terminal_proxy": "SceneWorks-mediated cwd, but NOT an OS sandbox",
                "persistent_sessions": "session/new + session/load; verified when Advanced session is created",
                "web_search_fetch": "Gemini-native; gated when permission requests are visible, not guaranteed OS/network sandboxing",
                "subagents": "Gemini-native (for example codebase investigator); gated when permission requests are visible",
                "client_mcp_servers": "not injected by SceneWorks in WP11",
                "advanced_permission_allowlist": list(
                    self.ctx.settings.advanced_session_permissions
                ),
            },
            "roles": roles,
        }

    async def _list_projects(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = _limit(args, 50, 200)
        async with self.ctx.engine_factory() as session:
            stmt = select(Project).order_by(Project.updated_at.desc()).limit(limit)
            status = _optional_str(args.get("status"))
            if status:
                stmt = stmt.where(Project.status == status)
            projects = (await session.execute(stmt)).scalars().all()
            rows = []
            for project in projects:
                tasks = (
                    await session.execute(
                        select(Task).where(Task.project_id == project.id)
                    )
                ).scalars().all()
                active = [
                    task
                    for task in tasks
                    if task.status not in {"ACCEPTED", "REJECTED", "CANCELLED"}
                ]
                rows.append(
                    {
                        "id": project.id,
                        "name": project.name,
                        "description": project.description,
                        "default_branch": project.default_branch,
                        "status": project.status,
                        "active_task_count": len(active),
                        "updated_at": _iso(project.updated_at),
                    }
                )
        return {"projects": rows}

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

        memories = await self.ctx.memory.search(
            project_id, query=query, status="accepted", limit=10
        )
        repo = Path(project.repository_path).resolve()
        try:
            info = await self.ctx.git.repo_info(repo)
            head = (
                await self.ctx.git.resolve_base_commit(repo, project.default_branch)
                if info.is_git
                else None
            )
            repo_snapshot: dict[str, Any] = {
                "is_git": info.is_git,
                "default_branch": project.default_branch,
                "head_commit": head,
                "error": info.error,
            }
        except Exception as exc:  # noqa: BLE001 - context read should degrade
            repo_snapshot = {
                "is_git": False,
                "default_branch": project.default_branch,
                "error": str(exc),
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
            "authority_note": (
                "Only accepted project memory is authoritative; agent outputs are "
                "execution evidence/inference until reviewed."
            ),
        }

    async def _list_tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = _limit(args, 50, 200)
        async with self.ctx.engine_factory() as session:
            stmt = select(Task).order_by(Task.updated_at.desc()).limit(limit)
            if args.get("project_id") is not None:
                stmt = stmt.where(Task.project_id == _int_arg(args, "project_id"))
            status = _optional_str(args.get("status"))
            if status:
                stmt = stmt.where(Task.status == status.upper())
            role = _optional_str(args.get("role"))
            if role:
                stmt = stmt.where(Task.current_role == role)
            tasks = (await session.execute(stmt)).scalars().all()
        return {"tasks": [_task_summary(task) for task in tasks]}

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
        provenance = await self.ctx.provenance.task(task_id)
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
            "executions": [_execution_summary(row) for row in executions],
            "events": [_event_row(event) for event in reversed(events)],
        }

    async def _get_task_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        task_id = _int_arg(args, "task_id")
        async with self.ctx.engine_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise MCPToolError(f"task {task_id} not found")
        if not task.worktree_path or not Path(task.worktree_path).is_dir():
            return {
                "task_id": task_id,
                "available": False,
                "reason": "task worktree is not available",
                "base_commit": task.base_commit,
                "result_commit": task.result_commit,
                "changed_files": list(task.changed_files or []),
            }
        base = task.base_commit or task.result_commit
        if not base:
            return {
                "task_id": task_id,
                "available": False,
                "reason": "no base commit recorded",
            }
        worktree = Path(task.worktree_path)
        try:
            diff = await self.ctx.git.diff(worktree, base)
            commits = await self.ctx.git.list_commits(worktree, base)
            status = await self.ctx.git.status(worktree)
        except Exception as exc:  # noqa: BLE001
            raise MCPToolError(f"could not read task diff: {exc}") from exc
        return {
            "task_id": task_id,
            "available": True,
            "base_commit": task.base_commit,
            "result_commit": task.result_commit,
            "stat": diff.get("stat", ""),
            "diff": diff.get("full", ""),
            "commits": commits,
            "status": status,
        }

    async def _get_execution(self, args: dict[str, Any]) -> dict[str, Any]:
        execution_id = str(args.get("execution_id") or "").strip()
        if not execution_id:
            raise MCPToolError("execution_id is required")
        event_limit = min(max(int(args.get("event_limit", 80)), 0), 200)
        async with self.ctx.engine_factory() as session:
            execution = await session.get(Execution, execution_id)
            if execution is None:
                raise MCPToolError(f"execution {execution_id} not found")
            events: list[Event] = []
            if event_limit:
                events = list(
                    (
                        await session.execute(
                            select(Event)
                            .where(Event.execution_id == execution_id)
                            .order_by(Event.id.desc())
                            .limit(event_limit)
                        )
                    ).scalars().all()
                )
        workspace = dict(execution.workspace or {})
        safe_workspace = {
            "branch": workspace.get("branch"),
            "base_commit": workspace.get("base_commit"),
            "permissions": workspace.get("permissions") or [],
            "project_id": workspace.get("project_id"),
        }
        return {
            "execution": {
                **_execution_summary(execution),
                "workspace": safe_workspace,
                "prompt_preview": execution.prompt_preview,
                "result": execution.result,
                "error": execution.error,
            },
            "events": [_event_row(event) for event in reversed(events)],
        }

    async def _search_memory(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = _int_arg(args, "project_id")
        status = str(args.get("status") or "accepted")
        types = args.get("types")
        if types is not None and not isinstance(types, list):
            raise MCPToolError("types must be an array")
        memories = await self.ctx.memory.search(
            project_id,
            query=str(args.get("query") or ""),
            types=types,
            status=status,
            limit=_limit(args, 20, 100),
        )
        return {
            "status_filter": status,
            "authoritative": status == "accepted",
            "memories": [_memory_row(memory) for memory in memories],
        }

    async def _list_artifacts(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = _limit(args, 20, 100)
        async with self.ctx.engine_factory() as session:
            stmt = select(Artifact).order_by(Artifact.created_at.desc()).limit(limit)
            if args.get("project_id") is not None:
                stmt = stmt.where(
                    Artifact.project_id == _int_arg(args, "project_id")
                )
            role = _optional_str(args.get("role"))
            if role:
                stmt = stmt.where(Artifact.role == role)
            kind = _optional_str(args.get("kind"))
            if kind:
                stmt = stmt.where(Artifact.kind == kind)
            rows = (await session.execute(stmt)).scalars().all()
        return {
            "artifacts": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "role": row.role,
                    "project_id": row.project_id,
                    "title": row.title,
                    "content": row.content,
                    "source_execution_id": row.source_execution_id,
                    "created_at": _iso(row.created_at),
                }
                for row in rows
            ]
        }

    # ---------------------------------------------------------- standard mode

    async def _inspect_repository(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_standard()
        project_id = _int_arg(args, "project_id")
        question = str(args.get("question") or "").strip()
        if not question:
            raise MCPToolError("question is required")
        execution = await self.ctx.company.ask("technical_expert", project_id, question)
        return {
            "execution": _execution_summary(execution),
            "next": (
                "Poll sceneworks.get_execution with execution_id="
                f"{execution.id!r} until terminal."
            ),
            "semantics": (
                "Read/shell analysis only; Technical Expert cannot modify source."
            ),
        }

    async def _ask_role(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_standard()
        role = str(args.get("role") or "").strip()
        question = str(args.get("question") or "").strip()
        if role not in ASK_ALLOWED_ROLES:
            raise MCPToolError(
                f"role must be one of: {', '.join(sorted(ASK_ALLOWED_ROLES))}"
            )
        if not question:
            raise MCPToolError("question is required")
        project_id = args.get("project_id")
        project_id = int(project_id) if project_id is not None else None
        execution = await self.ctx.company.ask(role, project_id, question)
        return {
            "execution": _execution_summary(execution),
            "next": (
                "Poll sceneworks.get_execution with execution_id="
                f"{execution.id!r} until terminal."
            ),
        }

    async def _create_task(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_standard()
        try:
            body = TaskCreate(
                project_id=_int_arg(args, "project_id"),
                work_package_id=(
                    int(args["work_package_id"])
                    if args.get("work_package_id") is not None
                    else None
                ),
                title=str(args.get("title") or ""),
                description=str(args.get("description") or ""),
                priority=str(args.get("priority") or "medium"),
                engineering_contract=EngineeringContract.model_validate(
                    args.get("engineering_contract") or {}
                ),
                capability_requirements=CapabilityProfile.model_validate(
                    args.get("capability_requirements") or {}
                ),
            )
        except Exception as exc:  # noqa: BLE001 - validation details useful
            raise MCPToolError(f"invalid task: {exc}") from exc

        async with self.ctx.engine_factory() as session:
            project = await session.get(Project, body.project_id)
            if project is None:
                raise MCPToolError(f"project {body.project_id} not found")
            if body.work_package_id is not None:
                work_package = await session.get(WorkPackage, body.work_package_id)
                if work_package is None:
                    raise MCPToolError(
                        f"work package {body.work_package_id} not found"
                    )
                initiative = await session.get(
                    Initiative, work_package.initiative_id
                )
                if initiative is None or initiative.project_id != body.project_id:
                    raise MCPToolError("work package belongs to a different project")
            task = Task(
                project_id=body.project_id,
                work_package_id=body.work_package_id,
                title=body.title,
                description=body.description,
                priority=body.priority,
                engineering_contract=body.engineering_contract.model_dump(),
                capability_requirements=body.capability_requirements.model_dump(),
                status=TaskStatus.NEW.value,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
        return {
            "task": _task_summary(task),
            "next": (
                "Review the task contract, then call sceneworks.task_action "
                "with start-architecture when ready."
            ),
        }

    async def _task_action(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_standard()
        task_id = _int_arg(args, "task_id")
        action = str(args.get("action") or "")
        reason = str(args.get("reason") or "")
        notes = str(args.get("notes") or "")
        manager = self.ctx.workflow_manager
        if action == "start-architecture":
            await manager.start_workflow(task_id)
        elif action == "approve-architecture":
            await manager.resume_approval(task_id, "approve")
        elif action == "reject-architecture":
            await manager.resume_approval(task_id, "reject", reason)
        elif action == "request-architecture-revision":
            await manager.resume_approval(task_id, "revision", notes)
        elif action == "start-implementation":
            await manager.start_implementation(task_id)
        elif action == "start-review":
            await manager.start_review(task_id)
        elif action == "accept":
            await self.ctx.provenance.capture_task_changes(task_id)
            await manager.accept(task_id)
        elif action == "reject":
            await self.ctx.provenance.capture_task_changes(task_id)
            await manager.reject(task_id, reason)
        elif action == "send-back":
            await manager.send_back_to_engineer(task_id, notes)
        elif action == "cancel":
            await manager.cancel(task_id)
        elif action == "retry":
            await manager.retry(task_id)
        elif action == "cleanup-worktree":
            await self.ctx.provenance.capture_task_changes(task_id)
            await manager.cleanup_worktree(task_id)
        else:
            raise MCPToolError(f"unknown task action: {action}")

        async with self.ctx.engine_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise MCPToolError(f"task {task_id} not found")
        return {"task": _task_summary(task), "action": action}

    # ---------------------------------------------------------- advanced mode

    async def _agent_session_list(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        project_id = (
            _int_arg(args, "project_id")
            if args.get("project_id") is not None
            else None
        )
        rows = await self.ctx.agent_sessions.list(
            project_id=project_id,
            limit=_limit(args, 50, 200),
        )
        return {"sessions": [_safe_session(row) for row in rows]}

    async def _agent_session_get(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        row = await self.ctx.agent_sessions.get(_int_arg(args, "session_id"))
        return {"session": _safe_session(row)}

    async def _agent_session_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        return await self.ctx.agent_sessions.diff(_int_arg(args, "session_id"))

    async def _agent_session_create(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        permissions = args.get("permissions")
        if permissions is not None and not isinstance(permissions, list):
            raise MCPToolError("permissions must be an array")
        row = await self.ctx.agent_sessions.create(
            _int_arg(args, "project_id"),
            permissions=[str(item) for item in permissions] if permissions else None,
            model=_optional_str(args.get("model")),
        )
        return {
            "session": _safe_session(row),
            "next": (
                "Use sceneworks.agent_session.prompt for iterative execution and "
                "poll sceneworks.agent_session.get until status returns ACTIVE."
            ),
            "safety": (
                "Files are worktree-confined. If shell_execute is enabled, shell "
                "commands still run with the SceneWorks OS user's authority."
            ),
        }

    async def _agent_session_prompt(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise MCPToolError("prompt is required")
        row = await self.ctx.agent_sessions.start_prompt(
            _int_arg(args, "session_id"), prompt
        )
        return {
            "session": _safe_session(row),
            "next": (
                "Poll sceneworks.agent_session.get until status is ACTIVE, FAILED "
                "or CLOSED. Then inspect agent_session.diff before further edits."
            ),
        }

    async def _agent_session_cancel(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        row = await self.ctx.agent_sessions.cancel(_int_arg(args, "session_id"))
        return {"session": _safe_session(row)}

    async def _agent_session_close(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        cleanup = args.get("cleanup_worktree", False)
        if not isinstance(cleanup, bool):
            raise MCPToolError("cleanup_worktree must be boolean")
        row = await self.ctx.agent_sessions.close(
            _int_arg(args, "session_id"), cleanup_worktree=cleanup
        )
        return {"session": _safe_session(row)}

    def _require_standard(self) -> None:
        if self.mode not in {"standard", "advanced"}:
            raise MCPToolError(
                "This tool requires Standard or Advanced MCP mode. Change the "
                "ChatGPT/MCP mode in SceneWorks Settings only after trusting the connection."
            )

    def _require_advanced(self) -> None:
        if self.mode != "advanced":
            raise MCPToolError(
                "This tool requires explicit Advanced MCP mode. Advanced mode lets "
                "the MCP client supervise Gemini CLI in an isolated worktree and may "
                "include shell/network authority selected by the operator."
            )

    # -------------------------------------------------------------- protocol

    async def handle(self, payload: Any) -> tuple[Any | None, int]:
        """Handle a JSON-RPC object or batch and return (body, HTTP status)."""
        if isinstance(payload, list):
            if not payload:
                return _rpc_error(None, -32600, "empty JSON-RPC batch"), 400
            replies = []
            for item in payload:
                reply = await self._handle_one(item)
                if reply is not None:
                    replies.append(reply)
            return (replies if replies else None), (200 if replies else 202)
        reply = await self._handle_one(payload)
        return reply, (200 if reply is not None else 202)

    async def _handle_one(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return _rpc_error(
                request.get("id") if isinstance(request, dict) else None,
                -32600,
                "invalid JSON-RPC request",
            )
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _rpc_error(request_id, -32602, "params must be an object")

        if method in {
            "notifications/initialized",
            "notifications/cancelled",
            "notifications/progress",
        }:
            return None

        try:
            if method == "server/discover":
                result = {
                    "resultType": "complete",
                    "supportedVersions": [LATEST_PROTOCOL, LEGACY_PROTOCOL],
                    "capabilities": {"tools": {"listChanged": False}},
                    "_meta": {
                        "io.modelcontextprotocol/serverInfo": {
                            "name": SERVER_NAME,
                            "version": __version__,
                        }
                    },
                    "instructions": _instructions(self.mode),
                    "ttlMs": 60000,
                    "cacheScope": "private",
                }
            elif method == "initialize":
                requested = str(params.get("protocolVersion") or LEGACY_PROTOCOL)
                negotiated = (
                    requested if requested in SUPPORTED_PROTOCOLS else LEGACY_PROTOCOL
                )
                result = {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                    "instructions": _instructions(self.mode),
                }
            elif method == "ping":
                result = {"resultType": "complete"}
            elif method == "tools/list":
                result = {
                    "resultType": "complete",
                    "tools": self.tool_definitions(),
                    "ttlMs": 60000,
                    "cacheScope": "private",
                }
            elif method == "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise MCPToolError("tool arguments must be an object")
                try:
                    structured = await self.call_tool(name, arguments)
                    result = _tool_result(structured, is_error=False)
                except MCPToolError as exc:
                    result = _tool_result(
                        {"error": str(exc), "tool": name}, is_error=True
                    )
            else:
                return _rpc_error(
                    request_id, -32601, f"method not found: {method}"
                )
        except Exception as exc:  # noqa: BLE001 - protocol boundary stays alive
            return _rpc_error(
                request_id,
                -32603,
                f"SceneWorks MCP internal error: {exc}",
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    annotations: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": annotations,
    }


def _instructions(mode: str) -> str:
    if mode == "observe":
        mode_text = "Observe mode: read-only semantic tools; no executions or mutations."
    elif mode == "standard":
        mode_text = (
            "Standard mode: semantic SceneWorks actions are enabled and remain "
            "governed by roles/workflows."
        )
    else:
        mode_text = (
            "Advanced mode: Standard tools plus persistent Gemini ACP execution "
            "sessions supervised by this MCP client."
        )
    return (
        "SceneWorks is the engineering control plane. Ground reasoning in project "
        "state, accepted memory, tasks, diffs and execution evidence. Raw host "
        "filesystem/shell/SQL/Git access is not an MCP capability. "
        + mode_text
    )


def _tool_result(structured: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    structured, ensure_ascii=False, default=str, indent=2
                ),
            }
        ],
        "structuredContent": structured,
        "isError": is_error,
    }


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _task_summary(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "work_package_id": task.work_package_id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "current_role": task.current_role,
        "current_execution_id": task.current_execution_id,
        "base_commit": task.base_commit,
        "result_commit": task.result_commit,
        "updated_at": _iso(task.updated_at),
    }


def _execution_summary(execution: Execution) -> dict[str, Any]:
    return {
        "id": execution.id,
        "task_id": execution.task_id,
        "role": execution.role,
        "backend": execution.backend,
        "model_profile": execution.model_profile,
        "model_name": execution.model_name,
        "status": execution.status,
        "created_at": _iso(execution.created_at),
        "started_at": _iso(execution.started_at),
        "finished_at": _iso(execution.finished_at),
    }


def _safe_session(row) -> dict[str, Any]:
    data = session_row(row, include_paths=False)
    # The provider's opaque session id is implementation state used by
    # SceneWorks for ACP session/load; the external reasoning client never needs it.
    data.pop("provider_session_id", None)
    data["provider_session_persisted"] = bool(row.provider_session_id)
    return data


def _memory_row(memory: ProjectMemory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "project_id": memory.project_id,
        "type": memory.type,
        "title": memory.title,
        "content": memory.content,
        "status": memory.status,
        "tags": list(memory.tags or []),
        "source": memory.source,
        "source_task_id": memory.source_task_id,
        "source_execution_id": memory.source_execution_id,
        "source_commit": memory.source_commit,
        "updated_at": _iso(memory.updated_at),
    }


def _event_row(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "execution_id": event.execution_id,
        "task_id": event.task_id,
        "type": event.type,
        "payload": event.payload,
        "severity": event.severity,
        "timestamp": _iso(event.timestamp),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _limit(args: dict[str, Any], default: int, maximum: int) -> int:
    return min(max(int(args.get("limit", default)), 1), maximum)


def _int_arg(args: dict[str, Any], name: str) -> int:
    value = args.get(name)
    if value is None:
        raise MCPToolError(f"{name} is required")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MCPToolError(f"{name} must be an integer") from exc
    if parsed < 1:
        raise MCPToolError(f"{name} must be >= 1")
    return parsed


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bounded(value: Any, max_chars: int) -> Any:
    """Bound large text fields recursively without losing provenance keys."""
    if max_chars <= 0:
        return value
    budget = [max_chars]

    def visit(item: Any) -> Any:
        if isinstance(item, str):
            if len(item) <= budget[0]:
                budget[0] -= len(item)
                return item
            remaining = max(budget[0], 0)
            budget[0] = 0
            return item[:remaining] + "\n[truncated by SceneWorks MCP response limit]"
        if isinstance(item, dict):
            return {str(key): visit(val) for key, val in item.items()}
        if isinstance(item, list):
            return [visit(val) for val in item]
        return item

    return visit(value)
