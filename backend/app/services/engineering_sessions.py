"""Provider-neutral direct engineering sessions (WP14).

An EngineeringSession owns a SceneWorks-created Git worktree plus a runtime and
permission ceiling. It does not own a model conversation. ChatGPT can therefore
inspect/run/debug directly even when every configured agent provider is down,
and may optionally delegate bounded work to any registered AgentBackend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.engineering_models import EngineeringSession
from app.git.workspace import GitError, GitWorktreeService
from app.models import Project
from app.runtime.base import ExecutionRuntime, RuntimeErrorBase
from app.runtime.registry import RuntimeRegistry

ENGINEERING_PERMISSION_NAMES = frozenset(
    {
        "repository_read",
        "repository_write",
        "shell_execute",
        "process_control",
        "git_commit",
        "network_access",
        "agent_delegate",
    }
)

TERMINAL_STATUSES = frozenset({"CLOSED"})


class EngineeringSessionError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EngineeringSessionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        git: GitWorktreeService,
        runtimes: RuntimeRegistry,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._git = git
        self._runtimes = runtimes
        self._settings = settings

    async def recover_interrupted(self) -> list[int]:
        """Direct sessions survive restarts; only in-progress creation is failed."""
        recovered: list[int] = []
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(EngineeringSession).where(
                            EngineeringSession.status == "STARTING"
                        )
                    )
                ).scalars().all()
            )
            for row in rows:
                row.status = "FAILED"
                row.metadata_json = {
                    **dict(row.metadata_json or {}),
                    "recovery_error": "SceneWorks restarted while the engineering session was being created",
                }
                row.updated_at = _now()
                recovered.append(row.id)
            if rows:
                await session.commit()
        return recovered

    async def create(
        self,
        project_id: int,
        *,
        permissions: list[str] | None = None,
        runtime: str = "native",
        default_backend: str | None = None,
        default_model: str | None = None,
    ) -> EngineeringSession:
        requested = permissions or [
            "repository_read",
            "repository_write",
            "shell_execute",
            "process_control",
        ]
        unknown = set(requested) - ENGINEERING_PERMISSION_NAMES
        if unknown:
            raise EngineeringSessionError(
                "unknown engineering-session permissions: " + ", ".join(sorted(unknown))
            )
        allowed = set(self._settings.advanced_session_permissions)
        # WP11 did not have these direct-runtime names. Treat process_control as
        # part of shell_execute and agent_delegate as available when subagents
        # were allowed, preserving old deployments while the setting migrates.
        if "shell_execute" in allowed:
            allowed.add("process_control")
        if "subagents" in allowed:
            allowed.add("agent_delegate")
        denied = set(requested) - allowed
        if denied:
            raise EngineeringSessionError(
                "permissions exceed the configured Advanced-mode ceiling: "
                + ", ".join(sorted(denied))
            )
        self._runtimes.get(runtime)

        async with self._session_factory() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise EngineeringSessionError(f"project {project_id} not found")
            repo_path = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo_path)
            if not info.is_git:
                raise EngineeringSessionError(info.error or "project repository is not a Git repository")
            base_commit = await self._git.resolve_base_commit(
                repo_path, project.default_branch or info.head_branch
            )
            row = EngineeringSession(
                project_id=project.id,
                runtime=runtime,
                status="STARTING",
                base_commit=base_commit,
                permissions=sorted(set(requested)),
                default_backend=default_backend or self._settings.default_backend,
                default_model=default_model,
                metadata_json={"repo_name": repo_path.name},
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            session_id = row.id

        branch = f"sw/mcp-{session_id}"
        try:
            workspace = await self._git.create_branch_worktree(
                repo_path, base_commit, session_id, branch
            )
        except GitError as exc:
            async with self._session_factory() as session:
                row = await session.get(EngineeringSession, session_id)
                if row is not None:
                    row.status = "FAILED"
                    row.metadata_json = {
                        **dict(row.metadata_json or {}),
                        "creation_error": str(exc),
                    }
                    row.updated_at = _now()
                    await session.commit()
            raise EngineeringSessionError(str(exc)) from exc

        async with self._session_factory() as session:
            row = await session.get(EngineeringSession, session_id)
            if row is None:
                raise EngineeringSessionError("engineering session disappeared during creation")
            row.branch = workspace.branch
            row.worktree_path = str(workspace.worktree_path)
            row.status = "ACTIVE"
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, session_id: int) -> EngineeringSession:
        async with self._session_factory() as session:
            row = await session.get(EngineeringSession, session_id)
            if row is None:
                raise EngineeringSessionError(f"engineering session {session_id} not found")
            return row

    async def list(self, project_id: int | None = None, limit: int = 50) -> list[EngineeringSession]:
        async with self._session_factory() as session:
            query = select(EngineeringSession).order_by(EngineeringSession.created_at.desc())
            if project_id is not None:
                query = query.where(EngineeringSession.project_id == project_id)
            return list((await session.execute(query.limit(limit))).scalars().all())

    async def runtime_for(
        self, session_id: int, permission: str
    ) -> tuple[EngineeringSession, ExecutionRuntime, Path]:
        row = await self.get(session_id)
        if row.status != "ACTIVE":
            raise EngineeringSessionError(
                f"engineering session {session_id} is {row.status.lower()}, not active"
            )
        if permission not in set(row.permissions or []):
            raise EngineeringSessionError(
                f"engineering session {session_id} does not grant {permission}"
            )
        if not row.worktree_path:
            raise EngineeringSessionError("engineering session has no worktree")
        worktree = Path(row.worktree_path).resolve()
        if not worktree.is_dir():
            raise EngineeringSessionError("engineering-session worktree is missing")
        try:
            runtime = self._runtimes.get(row.runtime)
        except RuntimeErrorBase as exc:
            raise EngineeringSessionError(str(exc)) from exc
        return row, runtime, worktree

    async def close(self, session_id: int, *, cleanup_worktree: bool = False) -> EngineeringSession:
        row = await self.get(session_id)
        if row.status == "CLOSED":
            return row
        async with self._session_factory() as session:
            project = await session.get(Project, row.project_id)
            if project is None:
                raise EngineeringSessionError(f"project {row.project_id} not found")
        if cleanup_worktree and row.worktree_path:
            worktree = Path(row.worktree_path).resolve()
            try:
                status = await self._git.status(worktree)
            except GitError as exc:
                raise EngineeringSessionError(str(exc)) from exc
            if status.strip():
                raise EngineeringSessionError(
                    "refusing to remove a dirty engineering-session worktree; commit or discard changes first"
                )
            try:
                # Preserve the branch/commits. Cleanup removes only the checked-out worktree.
                await self._git.remove_worktree(
                    Path(project.repository_path).resolve(), worktree, None
                )
            except GitError as exc:
                raise EngineeringSessionError(str(exc)) from exc

        async with self._session_factory() as session:
            current = await session.get(EngineeringSession, session_id)
            if current is None:
                raise EngineeringSessionError(f"engineering session {session_id} not found")
            current.status = "CLOSED"
            current.closed_at = _now()
            current.updated_at = _now()
            if cleanup_worktree:
                current.worktree_path = None
            await session.commit()
            await session.refresh(current)
            return current


def engineering_session_row(row: EngineeringSession) -> dict:
    """Public session representation; never return host filesystem paths."""
    return {
        "id": row.id,
        "project_id": row.project_id,
        "runtime": row.runtime,
        "status": row.status,
        "base_commit": row.base_commit,
        "branch": row.branch,
        "has_worktree": bool(row.worktree_path),
        "permissions": list(row.permissions or []),
        "default_backend": row.default_backend,
        "default_model": row.default_model,
        "metadata": dict(row.metadata_json or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
    }
