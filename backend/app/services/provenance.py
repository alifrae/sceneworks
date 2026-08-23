"""Queryable Git provenance over persisted task evidence (WP6).

Git remains authoritative for what changed. The service snapshots changed files
from the task worktree before terminal human actions so provenance survives
worktree cleanup. It never infers changes from agent summaries.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.git.workspace import GitWorktreeService
from app.models import ProjectMemory, Task


class ProvenanceService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        git: GitWorktreeService,
    ):
        self._session_factory = session_factory
        self._git = git

    async def capture_task_changes(self, task_id: int) -> list[str]:
        """Persist Git-observed changed paths for a task if evidence is available."""
        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return []
            worktree_path = task.worktree_path
            base_commit = task.base_commit

        if not worktree_path or not base_commit:
            return []
        worktree = Path(worktree_path)
        if not worktree.is_dir():
            return []

        changed = await self._git.changed_files(worktree, base_commit)
        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return changed
            task.changed_files = changed
            await session.commit()
        return changed

    async def task(self, task_id: int) -> dict | None:
        # Opportunistically persist evidence while the worktree still exists.
        # Terminal task actions also call capture explicitly before state change.
        await self.capture_task_changes(task_id)
        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return None
            memory_ids = list(
                (
                    await session.execute(
                        select(ProjectMemory.id)
                        .where(
                            ProjectMemory.source_task_id == task_id,
                            ProjectMemory.status == "accepted",
                        )
                        .order_by(ProjectMemory.id)
                    )
                ).scalars()
            )
            return self._row(task, memory_ids)

    async def project_history(
        self,
        project_id: int,
        *,
        path: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent task provenance, optionally limited to one exact path.

        Filtering is deliberately performed over the persisted normalised path
        list rather than relying on backend-specific JSON containment semantics.
        The candidate query is bounded, deterministic, and portable across the
        current SQLite deployment and a future relational backend.
        """
        normalized = path.strip().replace("\\", "/") if path and path.strip() else None
        candidate_limit = min(max(limit, 1) * 5, 500)
        async with self._session_factory() as session:
            tasks = list(
                (
                    await session.execute(
                        select(Task)
                        .where(Task.project_id == project_id)
                        .order_by(Task.updated_at.desc(), Task.id.desc())
                        .limit(candidate_limit)
                    )
                ).scalars()
            )
            if normalized is not None:
                tasks = [t for t in tasks if normalized in (t.changed_files or [])]
            tasks = tasks[: min(max(limit, 1), 200)]
            if not tasks:
                return []

            ids = [t.id for t in tasks]
            memory_rows = (
                await session.execute(
                    select(ProjectMemory.source_task_id, ProjectMemory.id)
                    .where(
                        ProjectMemory.source_task_id.in_(ids),
                        ProjectMemory.status == "accepted",
                    )
                    .order_by(ProjectMemory.id)
                )
            ).all()
            by_task: dict[int, list[int]] = {}
            for source_task_id, memory_id in memory_rows:
                if source_task_id is not None:
                    by_task.setdefault(source_task_id, []).append(memory_id)

        return [self._row(task, by_task.get(task.id, [])) for task in tasks]

    @staticmethod
    def _row(task: Task, memory_ids: list[int]) -> dict:
        return {
            "task_id": task.id,
            "project_id": task.project_id,
            "title": task.title,
            "status": task.status,
            "base_commit": task.base_commit,
            "result_commit": task.result_commit,
            "task_branch": task.task_branch,
            "changed_files": list(task.changed_files or []),
            "source_memory_ids": list(memory_ids),
        }
