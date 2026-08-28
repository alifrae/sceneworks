"""WP14 provider-neutral engineering-control extension for SceneWorks MCP."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.engineering_models import EngineeringSession
from app.models import Execution, Project
from app.mcp.server import MCPToolError, _bounded, _tool
from app.mcp.wp13_server import WorkManagementMCPServer
from app.runtime.base import ProcessSnapshot, RuntimeErrorBase
from app.services.engineering_sessions import (
    ENGINEERING_PERMISSION_NAMES,
    EngineeringSessionError,
    engineering_session_row,
)


class ProviderNeutralMCPServer(WorkManagementMCPServer):
    """Expose SceneWorks-owned execution independently of agent/model providers."""

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
        for tool in tools:
            name = str(tool.get("name") or "")
            if name.startswith("sceneworks.agent_session."):
                tool["description"] = "Legacy Gemini ACP provider session. " + str(
                    tool.get("description") or ""
                )

        if self.mode not in {"standard", "advanced"}:
            return tools

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

        tools.append(
            _tool(
                "sceneworks.register_project",
                "Register a Git repository already accessible on the SceneWorks host. The path is interpreted on that host, not on the MCP client.",
                {
                    "repository_path": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "name": {"type": "string", "maxLength": 200},
                    "description": {"type": "string", "maxLength": 5000, "default": ""},
                    "default_branch": {"type": "string", "maxLength": 200},
                },
                action,
                required=["repository_path"],
            )
        )

        if self.mode != "advanced":
            return tools

        session_id = {"type": "integer", "minimum": 1}
        relative_path = {
            "type": "string",
            "description": "Path relative to the EngineeringSession worktree. Absolute paths and path escapes are rejected.",
        }
        tools.extend(
            [
                _tool(
                    "sceneworks.engineering_session.list",
                    "List provider-neutral direct engineering sessions.",
                    {
                        "project_id": {"type": "integer", "minimum": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    read_only,
                ),
                _tool(
                    "sceneworks.engineering_session.get",
                    "Get a direct engineering session without exposing host filesystem paths.",
                    {"session_id": session_id},
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.engineering_session.create",
                    "Create an isolated Git branch/worktree for direct MCP engineering control. No model is invoked.",
                    {
                        "project_id": {"type": "integer", "minimum": 1},
                        "runtime": {
                            "type": "string",
                            "enum": self.ctx.runtimes.keys(),
                            "default": "native",
                        },
                        "permissions": {
                            "type": "array",
                            "items": {"type": "string", "enum": sorted(ENGINEERING_PERMISSION_NAMES)},
                            "description": "Optional subset of the configured Advanced-mode permission ceiling.",
                        },
                        "default_backend": {
                            "type": "string",
                            "enum": sorted(key for key in self.ctx.backends.keys() if key != "fake"),
                            "description": "Default delegated worker for this session; Gemini ACP remains the application default unless changed.",
                        },
                        "default_model": {"type": "string", "maxLength": 300},
                    },
                    action,
                    required=["project_id"],
                ),
                _tool(
                    "sceneworks.engineering_session.close",
                    "Close a direct engineering session. Optional cleanup removes only a clean worktree and preserves its branch/commits.",
                    {
                        "session_id": session_id,
                        "cleanup_worktree": {"type": "boolean", "default": False},
                    },
                    destructive,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.workspace.list",
                    "List files/directories inside an EngineeringSession worktree.",
                    {
                        "session_id": session_id,
                        "path": relative_path,
                        "recursive": {"type": "boolean", "default": False},
                        "max_entries": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 500},
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.workspace.read",
                    "Read UTF-8 text inside an EngineeringSession worktree.",
                    {
                        "session_id": session_id,
                        "path": relative_path,
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    read_only,
                    required=["session_id", "path"],
                ),
                _tool(
                    "sceneworks.workspace.search",
                    "Search text recursively inside an EngineeringSession worktree.",
                    {
                        "session_id": session_id,
                        "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "path": relative_path,
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    },
                    read_only,
                    required=["session_id", "query"],
                ),
                _tool(
                    "sceneworks.workspace.write",
                    "Atomically create/replace UTF-8 text inside an EngineeringSession worktree. expected_sha256 provides optimistic concurrency for edits.",
                    {
                        "session_id": session_id,
                        "path": relative_path,
                        "content": {"type": "string"},
                        "expected_sha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                        "create_only": {"type": "boolean", "default": False},
                    },
                    action,
                    required=["session_id", "path", "content"],
                ),
                _tool(
                    "sceneworks.command.run",
                    "Run one executable directly (no shell string evaluation) inside an EngineeringSession worktree and return captured output.",
                    {
                        "session_id": session_id,
                        "command": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                        "cwd": relative_path,
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 300},
                    },
                    action,
                    required=["session_id", "command"],
                ),
                _tool(
                    "sceneworks.process.start",
                    "Start a persistent child process in an EngineeringSession worktree (for example PCS or a dev server).",
                    {
                        "session_id": session_id,
                        "command": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                        "cwd": relative_path,
                    },
                    action,
                    required=["session_id", "command"],
                ),
                _tool(
                    "sceneworks.process.output",
                    "Read incremental stdout/stderr from a process started in this EngineeringSession.",
                    {
                        "session_id": session_id,
                        "process_id": {"type": "string", "minLength": 1},
                        "cursor": {"type": "integer", "minimum": 0, "default": 0},
                        "max_events": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
                    },
                    read_only,
                    required=["session_id", "process_id"],
                ),
                _tool(
                    "sceneworks.process.stop",
                    "Terminate a process started in this EngineeringSession.",
                    {
                        "session_id": session_id,
                        "process_id": {"type": "string", "minLength": 1},
                    },
                    destructive,
                    required=["session_id", "process_id"],
                ),
                _tool(
                    "sceneworks.git.status",
                    "Get branch, HEAD and working-tree status for an EngineeringSession.",
                    {"session_id": session_id},
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.git.diff",
                    "Get committed and uncommitted Git diff for an EngineeringSession; defaults to its pinned base commit.",
                    {"session_id": session_id},
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.git.commit",
                    "Stage and commit all current EngineeringSession changes.",
                    {
                        "session_id": session_id,
                        "message": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    destructive,
                    required=["session_id", "message"],
                ),
                _tool(
                    "sceneworks.agent.delegate",
                    "Delegate bounded work in the existing EngineeringSession worktree to any registered agent backend. ACP is not required; poll sceneworks.get_execution for completion.",
                    {
                        "session_id": session_id,
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 30000},
                        "backend": {
                            "type": "string",
                            "enum": sorted(key for key in self.ctx.backends.keys() if key != "fake"),
                        },
                        "model": {"type": "string", "maxLength": 300},
                    },
                    action,
                    required=["session_id", "prompt"],
                ),
            ]
        )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        args = arguments or {}
        handlers = {
            "sceneworks.register_project": self._register_project,
            "sceneworks.engineering_session.list": self._engineering_session_list,
            "sceneworks.engineering_session.get": self._engineering_session_get,
            "sceneworks.engineering_session.create": self._engineering_session_create,
            "sceneworks.engineering_session.close": self._engineering_session_close,
            "sceneworks.workspace.list": self._workspace_list,
            "sceneworks.workspace.read": self._workspace_read,
            "sceneworks.workspace.search": self._workspace_search,
            "sceneworks.workspace.write": self._workspace_write,
            "sceneworks.command.run": self._command_run,
            "sceneworks.process.start": self._process_start,
            "sceneworks.process.output": self._process_output,
            "sceneworks.process.stop": self._process_stop,
            "sceneworks.git.status": self._git_status,
            "sceneworks.git.diff": self._git_diff,
            "sceneworks.git.commit": self._git_commit,
            "sceneworks.agent.delegate": self._agent_delegate,
        }
        handler = handlers.get(name)
        if handler is None:
            return await super().call_tool(name, args)
        try:
            result = await handler(args)
        except MCPToolError:
            raise
        except (EngineeringSessionError, RuntimeErrorBase, ValueError, TypeError) as exc:
            raise MCPToolError(str(exc)) from exc
        return _bounded(result, int(self.ctx.settings.mcp_tool_max_chars))

    async def _register_project(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_standard()
        raw = str(args.get("repository_path") or "").strip()
        if not raw:
            raise MCPToolError("repository_path is required")
        path = Path(raw).expanduser().resolve()
        info = await self.ctx.git.repo_info(path)
        if not info.is_git:
            raise MCPToolError(info.error or f"not a Git repository: {raw}")

        async with self.ctx.engine_factory() as session:
            existing = list((await session.execute(select(Project))).scalars().all())
            for project in existing:
                try:
                    same = Path(project.repository_path).expanduser().resolve() == path
                except OSError:
                    same = project.repository_path == str(path)
                if same:
                    return {
                        "project": {
                            "id": project.id,
                            "name": project.name,
                            "default_branch": project.default_branch,
                            "already_registered": True,
                        },
                        "host_path_validated": True,
                    }

            default_branch = str(args.get("default_branch") or info.head_branch or "")
            project = Project(
                name=str(args.get("name") or path.name),
                description=str(args.get("description") or ""),
                repository_path=str(path),
                default_branch=default_branch,
                status="active",
                architecture_context_paths=[],
                test_commands=[],
                build_commands=[],
                engineering_policy={},
                capability_profile={},
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)
        return {
            "project": {
                "id": project.id,
                "name": project.name,
                "default_branch": project.default_branch,
                "head_commit": info.head_commit,
                "already_registered": False,
            },
            "host_path_validated": True,
            "note": "repository_path was resolved on the SceneWorks host",
        }

    async def _engineering_session_list(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        rows = await self.ctx.engineering_sessions.list(
            int(args["project_id"]) if args.get("project_id") is not None else None,
            max(1, min(200, int(args.get("limit") or 50))),
        )
        return {"sessions": [engineering_session_row(row) for row in rows]}

    async def _engineering_session_get(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        row = await self.ctx.engineering_sessions.get(int(args.get("session_id")))
        return {"session": engineering_session_row(row)}

    async def _engineering_session_create(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        backend = str(args.get("default_backend") or "").strip() or None
        if backend and backend not in self.ctx.backends.keys():
            raise MCPToolError(f"backend {backend!r} is not registered")
        row = await self.ctx.engineering_sessions.create(
            int(args.get("project_id")),
            permissions=(
                [str(item) for item in args.get("permissions", [])]
                if args.get("permissions") is not None
                else None
            ),
            runtime=str(args.get("runtime") or "native"),
            default_backend=backend,
            default_model=str(args.get("default_model") or "").strip() or None,
        )
        return {
            "session": engineering_session_row(row),
            "next": "Use workspace/git/command/process tools directly, or sceneworks.agent.delegate for an optional worker.",
        }

    async def _engineering_session_close(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        row = await self.ctx.engineering_sessions.close(
            int(args.get("session_id")),
            cleanup_worktree=bool(args.get("cleanup_worktree", False)),
        )
        return {"session": engineering_session_row(row)}

    async def _runtime(
        self, args: dict[str, Any], permission: str
    ) -> tuple[EngineeringSession, Any, Path]:
        self._require_advanced()
        return await self.ctx.engineering_sessions.runtime_for(
            int(args.get("session_id")), permission
        )

    async def _workspace_list(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "repository_read")
        return await runtime.list_files(
            root,
            str(args.get("path") or ""),
            recursive=bool(args.get("recursive", False)),
            max_entries=max(1, min(2000, int(args.get("max_entries") or 500))),
        )

    async def _workspace_read(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "repository_read")
        return await runtime.read_text(
            root,
            str(args.get("path") or ""),
            start_line=(int(args["start_line"]) if args.get("start_line") is not None else None),
            end_line=(int(args["end_line"]) if args.get("end_line") is not None else None),
            max_chars=int(self.ctx.settings.mcp_tool_max_chars),
        )

    async def _workspace_search(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "repository_read")
        return await runtime.search_text(
            root,
            str(args.get("query") or ""),
            path=str(args.get("path") or ""),
            max_results=max(1, min(500, int(args.get("max_results") or 100))),
        )

    async def _workspace_write(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "repository_write")
        return await runtime.write_text(
            root,
            str(args.get("path") or ""),
            str(args.get("content") or ""),
            expected_sha256=(
                str(args.get("expected_sha256")) if args.get("expected_sha256") else None
            ),
            create_only=bool(args.get("create_only", False)),
        )

    async def _command_run(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "shell_execute")
        result = await runtime.run_command(
            root,
            str(args.get("command") or ""),
            [str(item) for item in args.get("args", [])],
            cwd=str(args.get("cwd") or ""),
            timeout=max(1, min(3600, int(args.get("timeout") or 300))),
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def _process_start(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "process_control")
        snapshot = await runtime.start_process(
            root,
            str(args.get("command") or ""),
            [str(item) for item in args.get("args", [])],
            cwd=str(args.get("cwd") or ""),
        )
        return {"process": self._public_process(snapshot)}

    async def _process_output(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "process_control")
        snapshot = await runtime.process_output(
            str(args.get("process_id") or ""),
            cursor=max(0, int(args.get("cursor") or 0)),
            max_events=max(1, min(1000, int(args.get("max_events") or 200))),
        )
        self._assert_process_owned(snapshot, root)
        return {"process": self._public_process(snapshot)}

    async def _process_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "process_control")
        process_id = str(args.get("process_id") or "")
        existing = await runtime.process_output(process_id, cursor=0, max_events=1)
        self._assert_process_owned(existing, root)
        snapshot = await runtime.stop_process(process_id)
        return {"process": self._public_process(snapshot)}

    @staticmethod
    def _assert_process_owned(snapshot: ProcessSnapshot, root: Path) -> None:
        cwd = Path(snapshot.cwd).resolve()
        if not cwd.is_relative_to(root.resolve()):
            raise MCPToolError("process does not belong to this EngineeringSession")

    @staticmethod
    def _public_process(snapshot: ProcessSnapshot) -> dict[str, Any]:
        return {
            "process_id": snapshot.process_id,
            "command": snapshot.command,
            "running": snapshot.running,
            "returncode": snapshot.returncode,
            "next_cursor": snapshot.next_cursor,
            "output": snapshot.output,
        }

    async def _git_status(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "repository_read")
        return await runtime.git_status(root)

    async def _git_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        row, runtime, root = await self._runtime(args, "repository_read")
        return await runtime.git_diff(root, base_commit=row.base_commit)

    async def _git_commit(self, args: dict[str, Any]) -> dict[str, Any]:
        _, runtime, root = await self._runtime(args, "git_commit")
        return await runtime.git_commit(root, str(args.get("message") or ""))

    async def _agent_delegate(self, args: dict[str, Any]) -> dict[str, Any]:
        row, _, root = await self._runtime(args, "agent_delegate")
        backend = str(args.get("backend") or row.default_backend or self.ctx.settings.default_backend)
        if backend not in self.ctx.backends.keys() or backend == "fake":
            raise MCPToolError(f"backend {backend!r} is not available for delegation")
        model = str(args.get("model") or row.default_model or "").strip() or None
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise MCPToolError("prompt is required")

        execution = Execution(
            id=uuid.uuid4().hex,
            task_id=None,
            role="mcp_delegate",
            backend=backend,
            model_profile=None,
            model_name=model,
            status="QUEUED",
            workspace={
                "cwd": str(root),
                "repo_path": str(root),
                "branch": row.branch,
                "base_commit": row.base_commit,
                "permissions": list(row.permissions or []),
                "engineering_session_id": row.id,
            },
            system_prompt=(
                "You are a delegated engineering worker operating inside a SceneWorks-owned "
                "isolated Git worktree. Follow the user's bounded instruction, preserve existing "
                "interfaces unless required, inspect before changing, and report verification."
            ),
            user_prompt=prompt,
            prompt_preview=prompt[:2000],
        )
        async with self.ctx.engine_factory() as session:
            session.add(execution)
            await session.commit()
        await self.ctx.execution_engine.start(execution.id)
        return {
            "execution_id": execution.id,
            "backend": backend,
            "model": model,
            "engineering_session_id": row.id,
            "next": "Poll sceneworks.get_execution; inspect sceneworks.git.diff after completion.",
        }
