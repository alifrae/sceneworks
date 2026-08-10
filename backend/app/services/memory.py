"""Project Memory service.

Lightweight persistent project memory backed by SQLite text/metadata
retrieval. No embeddings, no vector DB, no knowledge graph.

Memory types: initiative_summary, architecture_decision, product_decision,
technology_decision, constraint.

Statuses: proposed, accepted, archived, superseded.

Provenance: every memory item records its source (manual, triage, architect,
etc.), optional source_task_id and source_execution_id.

Automatic extraction from speculative LLM output must remain proposed until
accepted. Never automatically convert speculation into accepted project truth.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.events import types as event_types
from app.events.bus import EventBus
from app.events.store import EventStore
from app.models import ProjectMemory

MEMORY_TYPES = {
    "initiative_summary",
    "architecture_decision",
    "product_decision",
    "technology_decision",
    "constraint",
}

MEMORY_STATUSES = {"proposed", "accepted", "archived", "superseded"}

MAX_INJECTION_BYTES = 20_000
MAX_INJECTION_ITEMS = 5


class MemoryService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_store: EventStore,
        bus: EventBus,
    ):
        self._session_factory = session_factory
        self._events = event_store
        self._bus = bus

    async def create(
        self,
        project_id: int,
        type: str,
        title: str,
        content: str,
        status: str = "proposed",
        tags: list[str] | None = None,
        source: str | None = None,
        source_task_id: int | None = None,
        source_execution_id: str | None = None,
    ) -> ProjectMemory:
        if type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory type: {type!r}")
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status!r}")

        async with self._session_factory() as session:
            mem = ProjectMemory(
                project_id=project_id,
                type=type,
                title=title,
                content=content,
                status=status,
                tags=tags or [],
                source=source or "manual",
                source_task_id=source_task_id,
                source_execution_id=source_execution_id,
            )
            session.add(mem)
            await session.commit()
            await session.refresh(mem)
            await self._emit_event(event_types.MEMORY_CREATED, mem)
            return mem

    async def get(self, memory_id: int) -> ProjectMemory | None:
        async with self._session_factory() as session:
            return await session.get(ProjectMemory, memory_id)

    async def update(
        self,
        memory_id: int,
        type: str | None = None,
        title: str | None = None,
        content: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> ProjectMemory | None:
        async with self._session_factory() as session:
            mem = await session.get(ProjectMemory, memory_id)
            if mem is None:
                return None
            if type is not None:
                if type not in MEMORY_TYPES:
                    raise ValueError(f"invalid memory type: {type!r}")
                mem.type = type
            if title is not None:
                mem.title = title
            if content is not None:
                mem.content = content
            if status is not None:
                if status not in MEMORY_STATUSES:
                    raise ValueError(f"invalid memory status: {status!r}")
                mem.status = status
            if tags is not None:
                mem.tags = tags
            if source is not None:
                mem.source = source
            await session.commit()
            await session.refresh(mem)
            await self._emit_event(event_types.MEMORY_UPDATED, mem)
            return mem

    async def archive(self, memory_id: int) -> ProjectMemory | None:
        async with self._session_factory() as session:
            mem = await session.get(ProjectMemory, memory_id)
            if mem is None:
                return None
            mem.status = "archived"
            await session.commit()
            await session.refresh(mem)
            await self._emit_event(event_types.MEMORY_ARCHIVED, mem)
            return mem

    async def supersede(self, memory_id: int, replacement_id: int) -> tuple[ProjectMemory | None, ProjectMemory | None]:
        async with self._session_factory() as session:
            old = await session.get(ProjectMemory, memory_id)
            replacement = await session.get(ProjectMemory, replacement_id)
            if old is None or replacement is None:
                return old, replacement
            old.status = "superseded"
            replacement.supersedes_id = old.id
            await session.commit()
            await session.refresh(old)
            await session.refresh(replacement)
            await self._emit_event(event_types.MEMORY_SUPERSEDED, old)
            return old, replacement

    async def search(
        self,
        project_id: int,
        query: str = "",
        types: Sequence[str] | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[ProjectMemory]:
        async with self._session_factory() as session:
            stmt = select(ProjectMemory).where(ProjectMemory.project_id == project_id)
            conditions = []

            if query.strip():
                q = f"%{query.strip()}%"
                conditions.append(
                    or_(
                        ProjectMemory.title.ilike(q),
                        ProjectMemory.content.ilike(q),
                    )
                )

            if types:
                conditions.append(ProjectMemory.type.in_(list(types)))

            if status:
                conditions.append(ProjectMemory.status == status)

            if tags:
                tag_conditions = []
                import json as _json
                for t in tags:
                    tag_conditions.append(ProjectMemory.tags.contains(_json.dumps(t)))
                if tag_conditions:
                    conditions.append(or_(*tag_conditions))

            if conditions:
                stmt = stmt.where(and_(*conditions))

            stmt = stmt.order_by(ProjectMemory.updated_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_relevant(
        self,
        project_id: int,
        query: str = "",
        types: Sequence[str] | None = None,
        limit: int = MAX_INJECTION_ITEMS,
    ) -> list[ProjectMemory]:
        """Deterministic retrieval for workflow injection.

        Returns only accepted memories, ordered by relevance (keyword
        match in title/content first, then recency).
        """
        async with self._session_factory() as session:
            stmt = select(ProjectMemory).where(
                and_(
                    ProjectMemory.project_id == project_id,
                    ProjectMemory.status == "accepted",
                )
            )

            if types:
                stmt = stmt.where(ProjectMemory.type.in_(list(types)))

            if query.strip():
                q = f"%{query.strip()}%"
                stmt = stmt.where(
                    or_(
                        ProjectMemory.title.ilike(q),
                        ProjectMemory.content.ilike(q),
                    )
                )

            stmt = stmt.order_by(ProjectMemory.updated_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def injection_context(
        self,
        project_id: int,
        task_description: str,
        types: Sequence[str] | None = None,
    ) -> dict:
        """Build bounded injection context for workflow nodes.

        Returns a dict with:
        - memories: list of matching accepted memories
        - total_bytes: encoded size
        - truncated: whether the context exceeded MAX_INJECTION_BYTES
        """
        items = await self.get_relevant(
            project_id, query=task_description, types=types,
        )

        result_items: list[dict] = []
        total = 0
        truncated = False

        for mem in items:
            entry = {
                "id": mem.id,
                "type": mem.type,
                "title": mem.title,
                "content": mem.content,
                "tags": mem.tags,
                "source": mem.source,
                "created_at": mem.created_at.isoformat() if mem.created_at else None,
            }
            entry_bytes = len(str(entry).encode("utf-8"))
            if total + entry_bytes > MAX_INJECTION_BYTES:
                truncated = True
                break
            result_items.append(entry)
            total += entry_bytes

        return {
            "memories": result_items,
            "total_bytes": total,
            "truncated": truncated,
            "injected_ids": [m["id"] for m in result_items],
        }

    async def list_project_memories(
        self,
        project_id: int,
        status: str | None = None,
    ) -> list[ProjectMemory]:
        async with self._session_factory() as session:
            stmt = select(ProjectMemory).where(
                ProjectMemory.project_id == project_id
            )
            if status:
                stmt = stmt.where(ProjectMemory.status == status)
            stmt = stmt.order_by(ProjectMemory.updated_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def _emit_event(self, event_type: str, mem: ProjectMemory) -> None:
        payload = {
            "id": mem.id,
            "project_id": mem.project_id,
            "type": mem.type,
            "title": mem.title,
            "status": mem.status,
        }
        row = await self._events.append(
            execution_id=None,
            task_id=mem.source_task_id,
            type=event_type,
            payload=payload,
        )
        # Publish the persisted row id: SSE clients de-duplicate on it, so a
        # hard-coded 0 made every memory event after the first look like a
        # replay of the first and get dropped.
        await self._bus.publish({
            "id": row.id,
            "execution_id": None,
            "task_id": mem.source_task_id,
            "type": event_type,
            "payload": payload,
            "severity": "info",
            "timestamp": row.timestamp.isoformat(),
        })
