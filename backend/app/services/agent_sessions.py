"""Persistent ChatGPT-supervised Gemini ACP sessions (WP11 Advanced mode).

The session is persistent at the *provider conversation + Git workspace* level,
not by keeping one Gemini process alive forever. SceneWorks stores Gemini's ACP
session id and the isolated worktree. Every turn starts a fresh ACP process,
loads the provider session with ``session/load``, runs one prompt, then releases
the process. This survives SceneWorks restarts and avoids orphaned long-lived
agent processes while preserving iterative model context.

Advanced mode is deliberately separate from the governed Task workflow. It is
an explicit operator-controlled escape hatch in which the external MCP client
(ChatGPT) is the supervisor and Gemini CLI is an execution subagent. SceneWorks
still owns workspace creation, permission mediation, cancellation and Git
provenance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.gemini_acp import (
    AcpConnectionClosed,
    AcpError,
    AcpStdioClient,
    AgentPolicy,
    GeminiACPBackend,
    _join_text,
)
from app.config.settings import Settings
from app.events.store import EventStore
from app.git.workspace import GitError, GitWorktreeService
from app.models import AgentSession, Project

ADVANCED_PERMISSION_NAMES = frozenset(
    {
        "repository_read",
        "repository_write",
        "shell_execute",
        "git_commit",
        "network_access",
        "subagents",
    }
)

_TERMINAL_SESSION_STATUSES = {"CLOSED"}
_RECOVERABLE_SESSION_STATUSES = {"ACTIVE", "FAILED", "INTERRUPTED"}


class AgentSessionError(RuntimeError):
    pass


@dataclass
class AdvancedAgentPolicy(AgentPolicy):
    """Additional policy used only by external-supervisor sessions.

    ACP cannot provide a hard OS sandbox for shell commands. These checks gate
    the protocol-mediated capabilities and obvious network/Git commands, while
    the UI/documentation still warns that enabling shell delegates the authority
    of the SceneWorks OS user inside the configured worktree process.
    """

    allow_network: bool = False
    allow_subagents: bool = False
    allow_git_commit: bool = False


class AdvancedAcpStdioClient(AcpStdioClient):
    """AcpStdioClient with Advanced-mode network/subagent/Git permission gates."""

    _NETWORK_COMMANDS = {
        "curl",
        "curl.exe",
        "wget",
        "wget.exe",
        "ssh",
        "ssh.exe",
        "scp",
        "scp.exe",
        "ftp",
        "ftp.exe",
    }

    @property
    def _advanced_policy(self) -> AdvancedAgentPolicy:
        policy = self._policy
        if not isinstance(policy, AdvancedAgentPolicy):
            raise AcpError("advanced ACP client requires AdvancedAgentPolicy")
        return policy

    async def _serve_permission(self, params: dict) -> dict:
        policy = self._advanced_policy
        tool_call = params.get("toolCall") or {}
        kind = str(tool_call.get("kind") or "other").lower()
        title = str(tool_call.get("title") or "tool")
        key = title.lower().replace("-", "_")

        is_network = kind == "fetch" or any(
            marker in key
            for marker in ("web_fetch", "web fetch", "web_search", "web search", "google search", "browser", "http")
        )
        is_subagent = any(
            marker in key
            for marker in ("subagent", "sub_agent", "codebase_investigator", "codebase investigator")
        )
        if is_network and not policy.allow_network:
            return await self._deny_permission(params, "network capability disabled")
        if is_subagent and not policy.allow_subagents:
            return await self._deny_permission(params, "Gemini subagents disabled")
        return await super()._serve_permission(params)

    async def _deny_permission(self, params: dict, reason: str) -> dict:
        tool_call = params.get("toolCall") or {}
        title = str(tool_call.get("title") or "tool")
        await self._sink.emit(
            "agent.event",
            {
                "name": "permission_denied",
                "tool": title,
                "kind": tool_call.get("kind") or "other",
                "reason": reason,
                "diagnostics": True,
            },
            severity="warning",
        )
        for option in params.get("options") or []:
            if str(option.get("name") or "") in {
                "reject",
                "reject_once",
                "reject_always",
                "Deny",
                "Reject",
            }:
                return {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": option.get("optionId"),
                    }
                }
        return {"outcome": {"outcome": "cancelled"}}

    async def _serve_terminal_create(self, params: dict) -> dict:
        policy = self._advanced_policy
        command = str(params.get("command") or "")
        command_name = Path(command).name.lower()
        args = [str(item) for item in (params.get("args") or [])]
        git_verb = args[0].lower() if command_name in {"git", "git.exe"} and args else ""

        if git_verb == "commit" and not policy.allow_git_commit:
            await self._sink.emit(
                "agent.event",
                {
                    "name": "git_commit_denied",
                    "command": " ".join([command, *args])[:500],
                    "diagnostics": True,
                },
                severity="warning",
            )
            raise AcpError("git commit disabled for this advanced session")

        obvious_network = command_name in self._NETWORK_COMMANDS or (
            command_name in {"git", "git.exe"}
            and git_verb in {"clone", "fetch", "pull", "push", "ls-remote"}
        )
        if obvious_network and not policy.allow_network:
            await self._sink.emit(
                "agent.event",
                {
                    "name": "network_command_denied",
                    "command": " ".join([command, *args])[:500],
                    "diagnostics": True,
                },
                severity="warning",
            )
            raise AcpError("network access disabled for this advanced session")

        return await super()._serve_terminal_create(params)


class _SessionEventSink:
    def __init__(self, session_id: int, event_store: EventStore):
        self.session_id = session_id
        self._events = event_store
        self._cancel_event = asyncio.Event()

    async def emit(self, type: str, payload: dict, severity: str = "info") -> None:
        await self._events.append(
            execution_id=None,
            task_id=None,
            type=f"agent_session.{type}",
            payload={"agent_session_id": self.session_id, **payload},
            severity=severity,
        )

    def cancel(self) -> None:
        self._cancel_event.set()

    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    async def wait_for_cancel(self) -> None:
        await self._cancel_event.wait()


@dataclass
class _ActiveTurn:
    task: asyncio.Task
    sink: _SessionEventSink
    client: AdvancedAcpStdioClient | None = None
    provider_session_id: str | None = None


class AgentSessionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        git: GitWorktreeService,
        events: EventStore,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._git = git
        self._events = events
        self._settings = settings
        self._active: dict[int, _ActiveTurn] = {}
        self._lock = asyncio.Lock()

    async def recover_interrupted(self) -> list[int]:
        """Mark sessions left mid-turn by a previous SceneWorks process."""
        recovered: list[int] = []
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(AgentSession).where(
                        AgentSession.status.in_(["STARTING", "RUNNING"])
                    )
                )
            ).scalars().all()
            for row in rows:
                row.status = "INTERRUPTED"
                row.last_error = "SceneWorks restarted while the advanced session was running"
                row.updated_at = _now()
                recovered.append(row.id)
            if rows:
                await session.commit()
        return recovered

    async def shutdown(self) -> None:
        for session_id in list(self._active):
            try:
                await self.cancel(session_id)
            except AgentSessionError:
                pass
        tasks = [turn.task for turn in self._active.values() if not turn.task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()

    async def create(
        self,
        project_id: int,
        *,
        permissions: list[str] | None = None,
        model: str | None = None,
    ) -> AgentSession:
        requested = permissions or list(self._settings.advanced_session_permissions)
        effective = self._normalize_permissions(requested)
        if "repository_read" not in effective:
            effective.insert(0, "repository_read")
        if "repository_write" in effective and "shell_execute" not in effective:
            # Gemini can still use fs/write without shell; keep that valid. No implication.
            pass

        async with self._session_factory() as db:
            project = await db.get(Project, project_id)
            if project is None:
                raise AgentSessionError(f"project {project_id} not found")
            repo = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise AgentSessionError(info.error or f"{repo} is not a Git repository")
            base = await self._git.resolve_base_commit(repo, project.default_branch)
            row = AgentSession(
                project_id=project_id,
                backend="gemini_acp",
                status="STARTING",
                base_commit=base,
                permissions=effective,
                model_name=model or self._settings.gemini_model,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            session_id = row.id

        worktree = None
        branch: str | None = None
        try:
            if "repository_write" in effective:
                branch = f"sw-agent-session-{session_id}"
                worktree = await self._git.create_branch_worktree(
                    repo, base, session_id, branch
                )
            else:
                worktree = await self._git.create_snapshot_worktree(
                    repo, base, f"agent-session-{session_id}"
                )

            provider_session_id, capabilities = await self._new_provider_session(
                session_id,
                repo=repo,
                worktree=worktree.worktree_path,
                permissions=effective,
                model=model or self._settings.gemini_model,
            )
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                row.provider_session_id = provider_session_id
                row.status = "ACTIVE"
                row.branch = branch
                row.worktree_path = str(worktree.worktree_path)
                row.provider_capabilities = capabilities
                row.last_error = None
                row.updated_at = _now()
                await db.commit()
                await db.refresh(row)
            await self._event(
                session_id,
                "created",
                {
                    "project_id": project_id,
                    "base_commit": base,
                    "branch": branch,
                    "permissions": effective,
                    "provider_session_id": provider_session_id,
                },
            )
            return row
        except Exception as exc:
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                row.status = "FAILED"
                row.last_error = str(exc)
                row.updated_at = _now()
                await db.commit()
            if worktree is not None:
                try:
                    await self._git.remove_worktree(repo, worktree.worktree_path, branch)
                except GitError:
                    pass
            if isinstance(exc, AgentSessionError):
                raise
            raise AgentSessionError(str(exc)) from exc

    async def start_prompt(self, session_id: int, prompt: str) -> AgentSession:
        text = prompt.strip()
        if not text:
            raise AgentSessionError("prompt is required")
        async with self._lock:
            active = self._active.get(session_id)
            if active and not active.task.done():
                raise AgentSessionError(f"agent session {session_id} already has a running turn")
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                if row.status in _TERMINAL_SESSION_STATUSES:
                    raise AgentSessionError(f"agent session {session_id} is closed")
                if row.status not in _RECOVERABLE_SESSION_STATUSES:
                    raise AgentSessionError(
                        f"agent session {session_id} cannot prompt while {row.status}"
                    )
                if not row.provider_session_id or not row.worktree_path:
                    raise AgentSessionError("advanced session is missing provider/worktree state")
                row.status = "RUNNING"
                row.last_result = None
                row.last_error = None
                row.updated_at = _now()
                await db.commit()
                await db.refresh(row)
            sink = _SessionEventSink(session_id, self._events)
            task = asyncio.create_task(
                self._run_prompt(session_id, text, sink),
                name=f"advanced-agent-session-{session_id}",
            )
            self._active[session_id] = _ActiveTurn(task=task, sink=sink)
            task.add_done_callback(lambda _task, sid=session_id: self._active.pop(sid, None))
            await self._event(
                session_id,
                "prompt_started",
                {"prompt_preview": text[:1000]},
            )
            return row

    async def get(self, session_id: int) -> AgentSession:
        async with self._session_factory() as db:
            return await self._get_row(db, session_id)

    async def list(self, project_id: int | None = None, limit: int = 50) -> list[AgentSession]:
        async with self._session_factory() as db:
            stmt = select(AgentSession).order_by(AgentSession.updated_at.desc()).limit(
                min(max(limit, 1), 200)
            )
            if project_id is not None:
                stmt = stmt.where(AgentSession.project_id == project_id)
            return list((await db.execute(stmt)).scalars().all())

    async def diff(self, session_id: int) -> dict[str, Any]:
        row = await self.get(session_id)
        if not row.worktree_path:
            raise AgentSessionError("session has no worktree")
        worktree = Path(row.worktree_path)
        if not worktree.is_dir():
            raise AgentSessionError("session worktree is no longer available")
        diff = await self._git.diff(worktree, row.base_commit)
        commits = await self._git.list_commits(worktree, row.base_commit)
        status = await self._git.status(worktree)
        head = await self._git.head_commit(worktree)
        return {
            "session_id": row.id,
            "base_commit": row.base_commit,
            "head_commit": head,
            "branch": row.branch,
            "stat": diff.get("stat", ""),
            "full": diff.get("full", ""),
            "commits": commits,
            "status": status,
        }

    async def cancel(self, session_id: int) -> AgentSession:
        active = self._active.get(session_id)
        if active and not active.task.done():
            active.sink.cancel()
            if active.client is not None and active.provider_session_id:
                try:
                    await active.client.request(
                        "session/cancel",
                        {"sessionId": active.provider_session_id},
                        timeout=10,
                    )
                except (AcpError, AcpConnectionClosed):
                    pass
            active.task.cancel()
            try:
                await active.task
            except (asyncio.CancelledError, AgentSessionError):
                pass
        async with self._session_factory() as db:
            row = await self._get_row(db, session_id)
            if row.status == "RUNNING":
                row.status = "ACTIVE"
                row.last_error = "turn cancelled by supervisor"
                row.updated_at = _now()
                await db.commit()
                await db.refresh(row)
        await self._event(session_id, "cancelled", {})
        return row

    async def close(self, session_id: int, *, cleanup_worktree: bool = False) -> AgentSession:
        await self.cancel(session_id)
        async with self._session_factory() as db:
            row = await self._get_row(db, session_id)
            if row.status != "CLOSED":
                row.status = "CLOSED"
                row.closed_at = _now()
                row.updated_at = row.closed_at
                await db.commit()
                await db.refresh(row)
            project = await db.get(Project, row.project_id)

        if cleanup_worktree and row.worktree_path and project is not None:
            worktree = Path(row.worktree_path)
            if worktree.exists():
                status = await self._git.status(worktree)
                if status.strip():
                    raise AgentSessionError(
                        "refusing to remove an advanced-session worktree with uncommitted changes"
                    )
                # Preserve the branch and its commits. Passing branch=None removes
                # only the worktree registration/path.
                await self._git.remove_worktree(
                    Path(project.repository_path).resolve(), worktree, None
                )
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                row.worktree_path = None
                row.updated_at = _now()
                await db.commit()
                await db.refresh(row)
        await self._event(
            session_id,
            "closed",
            {"cleanup_worktree": cleanup_worktree, "branch_preserved": bool(row.branch)},
        )
        return row

    async def _run_prompt(
        self,
        session_id: int,
        prompt: str,
        sink: _SessionEventSink,
    ) -> None:
        client: AdvancedAcpStdioClient | None = None
        provider_session_id: str | None = None
        try:
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                project = await db.get(Project, row.project_id)
                if project is None:
                    raise AgentSessionError(f"project {row.project_id} not found")
                repo = Path(project.repository_path).resolve()
                worktree = Path(row.worktree_path or "").resolve()
                permissions = list(row.permissions or [])
                provider_session_id = row.provider_session_id
                model = row.model_name
            if not worktree.is_dir():
                raise AgentSessionError("advanced-session worktree no longer exists")
            if not provider_session_id:
                raise AgentSessionError("advanced session has no provider session id")

            client, init = await self._open_client(
                session_id,
                repo=repo,
                worktree=worktree,
                permissions=permissions,
                model=model,
                sink=sink,
            )
            active = self._active.get(session_id)
            if active is not None:
                active.client = client
                active.provider_session_id = provider_session_id

            capabilities = init.get("agentCapabilities") or {}
            if not capabilities.get("loadSession"):
                raise AgentSessionError(
                    "installed Gemini CLI does not advertise ACP loadSession; persistent Advanced mode requires it"
                )
            await client.request(
                "session/load",
                {
                    "sessionId": provider_session_id,
                    "cwd": str(worktree),
                    "mcpServers": [],
                },
                timeout=self._settings.gemini_startup_timeout_seconds,
            )

            supervisor_prompt = self._supervisor_prompt(prompt, permissions)
            prompt_task = asyncio.create_task(
                client.request(
                    "session/prompt",
                    {
                        "sessionId": provider_session_id,
                        "prompt": [{"type": "text", "text": supervisor_prompt}],
                    },
                    timeout=self._settings.execution_timeout_seconds + 30,
                )
            )
            cancel_wait = asyncio.create_task(sink.wait_for_cancel())
            done, _ = await asyncio.wait(
                {prompt_task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_wait in done and prompt_task not in done:
                try:
                    await client.request(
                        "session/cancel", {"sessionId": provider_session_id}, timeout=10
                    )
                except (AcpError, AcpConnectionClosed):
                    pass
                prompt_task.cancel()
                raise asyncio.CancelledError
            cancel_wait.cancel()
            result = prompt_task.result()
            stop_reason = result.get("stopReason") or "end_turn"
            text = _join_text(client._final_message) or "Agent finished."
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                row.status = "ACTIVE"
                row.last_result = text
                row.last_error = None if stop_reason not in {"refusal"} else f"stopReason={stop_reason}"
                row.updated_at = _now()
                await db.commit()
            await self._event(
                session_id,
                "prompt_completed",
                {"stop_reason": stop_reason, "result_preview": text[:2000]},
            )
        except asyncio.CancelledError:
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                if row.status == "RUNNING":
                    row.status = "ACTIVE"
                    row.last_error = "turn cancelled by supervisor"
                    row.updated_at = _now()
                    await db.commit()
            raise
        except Exception as exc:
            async with self._session_factory() as db:
                row = await self._get_row(db, session_id)
                row.status = "FAILED"
                row.last_error = str(exc)
                row.updated_at = _now()
                await db.commit()
            await self._event(
                session_id,
                "prompt_failed",
                {"error": f"{type(exc).__name__}: {exc}"[:2000]},
                severity="error",
            )
        finally:
            if client is not None:
                if provider_session_id:
                    try:
                        await client.request(
                            "session/close",
                            {"sessionId": provider_session_id},
                            timeout=5,
                        )
                    except (AcpError, AcpConnectionClosed):
                        pass
                await client.stop()

    async def _new_provider_session(
        self,
        session_id: int,
        *,
        repo: Path,
        worktree: Path,
        permissions: list[str],
        model: str | None,
    ) -> tuple[str, dict[str, Any]]:
        sink = _SessionEventSink(session_id, self._events)
        client, init = await self._open_client(
            session_id,
            repo=repo,
            worktree=worktree,
            permissions=permissions,
            model=model,
            sink=sink,
        )
        provider_session_id: str | None = None
        try:
            result = await client.request(
                "session/new",
                {"cwd": str(worktree), "mcpServers": []},
                timeout=self._settings.gemini_startup_timeout_seconds,
            )
            provider_session_id = str(result.get("sessionId") or "").strip()
            if not provider_session_id:
                raise AgentSessionError("Gemini ACP session/new returned no sessionId")
            capabilities = {
                "protocol_version": init.get("protocolVersion"),
                "agent_info": init.get("agentInfo") or {},
                "agent_capabilities": init.get("agentCapabilities") or {},
                "session": {
                    "modes": result.get("modes") or {},
                    "models": result.get("models") or {},
                    "config_options": result.get("configOptions") or [],
                },
            }
            return provider_session_id, capabilities
        finally:
            if provider_session_id:
                try:
                    await client.request(
                        "session/close", {"sessionId": provider_session_id}, timeout=5
                    )
                except (AcpError, AcpConnectionClosed):
                    pass
            await client.stop()

    async def _open_client(
        self,
        session_id: int,
        *,
        repo: Path,
        worktree: Path,
        permissions: list[str],
        model: str | None,
        sink: _SessionEventSink,
    ) -> tuple[AdvancedAcpStdioClient, dict[str, Any]]:
        backend = GeminiACPBackend(self._settings)
        launch = backend._launch_command()  # provider-specific service by design
        if not launch:
            raise AgentSessionError(
                "Gemini CLI not found on PATH (set SCENEWORKS_GEMINI_EXECUTABLE)"
            )
        env = dict(self._settings.gemini_environment)
        if model and "GEMINI_MODEL" not in env:
            env["GEMINI_MODEL"] = model
        permission_set = set(permissions)
        policy = AdvancedAgentPolicy(
            workspace_root=worktree,
            repo_root=repo,
            allow_write="repository_write" in permission_set,
            allow_shell="shell_execute" in permission_set,
            allow_network="network_access" in permission_set,
            allow_subagents="subagents" in permission_set,
            allow_git_commit="git_commit" in permission_set,
        )
        client = AdvancedAcpStdioClient(
            launch_args=launch + ["--acp"] + list(self._settings.gemini_extra_args),
            environment=env,
            cwd=str(worktree),
            sink=sink,
            policy=policy,
        )
        await client.start()
        try:
            init = await client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "auth": {"terminal": False},
                        "fs": {"readTextFile": True, "writeTextFile": True},
                        "terminal": True,
                    },
                    "clientInfo": {"name": "SceneWorks Advanced", "version": "1.0"},
                },
                timeout=self._settings.gemini_startup_timeout_seconds,
            )
            await sink.emit(
                "agent.event",
                {
                    "name": "backend.initialized",
                    "agent_session_id": session_id,
                    "agent_info": init.get("agentInfo") or {},
                    "agent_capabilities": init.get("agentCapabilities") or {},
                },
            )
            return client, init
        except Exception:
            await client.stop()
            raise

    def _normalize_permissions(self, permissions: list[str]) -> list[str]:
        configured = set(self._settings.advanced_session_permissions)
        unknown = set(permissions) - ADVANCED_PERMISSION_NAMES
        if unknown:
            raise AgentSessionError(
                "unknown advanced-session permissions: " + ", ".join(sorted(unknown))
            )
        disallowed = set(permissions) - configured
        if disallowed:
            raise AgentSessionError(
                "permissions disabled by SceneWorks settings: " + ", ".join(sorted(disallowed))
            )
        return [name for name in ADVANCED_PERMISSION_NAMES if name in permissions]

    @staticmethod
    def _supervisor_prompt(prompt: str, permissions: list[str]) -> str:
        return (
            "# SceneWorks Advanced session\n"
            "You are the execution subagent of an external reasoning supervisor. "
            "Work only on the supervisor's current request. Inspect before editing; "
            "prefer the smallest compatible change; run targeted verification; "
            "do not broaden scope or modify unrelated code. SceneWorks mediates "
            "your workspace/tool permissions.\n\n"
            f"Effective permissions: {', '.join(permissions)}\n\n"
            f"# Supervisor request\n{prompt}"
        )

    async def _get_row(self, db: AsyncSession, session_id: int) -> AgentSession:
        row = await db.get(AgentSession, session_id)
        if row is None:
            raise AgentSessionError(f"agent session {session_id} not found")
        return row

    async def _event(
        self,
        session_id: int,
        name: str,
        payload: dict[str, Any],
        severity: str = "info",
    ) -> None:
        await self._events.append(
            execution_id=None,
            task_id=None,
            type=f"agent_session.{name}",
            payload={"agent_session_id": session_id, **payload},
            severity=severity,
        )


def session_row(row: AgentSession, *, include_paths: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "project_id": row.project_id,
        "backend": row.backend,
        "provider_session_id": row.provider_session_id,
        "status": row.status,
        "base_commit": row.base_commit,
        "branch": row.branch,
        "permissions": list(row.permissions or []),
        "model_name": row.model_name,
        "provider_capabilities": row.provider_capabilities or {},
        "last_result": row.last_result,
        "last_error": row.last_error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "closed_at": _iso(row.closed_at),
    }
    if include_paths:
        data["worktree_path"] = row.worktree_path
    else:
        data["worktree_available"] = bool(row.worktree_path)
    return data


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
