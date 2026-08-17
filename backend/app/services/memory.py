"""Project Memory service.

Persistent project memory backed by SQLite with deterministic, explainable
retrieval. No embeddings, no vector DB, no knowledge graph — and none are needed
until deterministic retrieval demonstrably falls short.

Memory types: initiative_summary, architecture_decision, product_decision,
technology_decision, constraint.

Lifecycle
---------
    task / execution / result
        -> candidate decision, constraint or initiative knowledge
        -> proposed          (anything an agent produced)
        -> human review
        -> accepted | rejected | superseded | archived

Only `accepted` memories are injected as authoritative project context.
Speculative agent output enters as `proposed` and stays there until a human acts;
`propose_from_execution()` refuses to create anything else. Proposed memories are
returned separately by `injection_context()` so a UI can show them without them
influencing implementation as though they were settled.

Provenance
----------
Every item records `source` (the authoring role or "manual"), `source_task_id`,
`source_execution_id`, `supersedes_id` and timestamps. Review provenance is
recorded as an event, so who accepted what and when survives in the event log.

Retrieval
---------
See `memory_retrieval.py`. Scoring is term-based, not sentence-based: passing a
whole task description to one SQL `ILIKE` retrieved nothing, because no stored
memory contains a task description verbatim (docs/wp0-baseline-audit.md, F4).
"""

from __future__ import annotations

import json
from typing import Sequence

from sqlalchemy import Text, and_, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.events import types as event_types
from app.events.bus import EventBus
from app.events.store import EventStore
from app.models import ProjectMemory
from app.services.memory_retrieval import (
    Candidate,
    MemoryMatch,
    rank,
    tokenize,
)

MEMORY_TYPES = {
    "initiative_summary",
    "architecture_decision",
    "product_decision",
    "technology_decision",
    "constraint",
}

#: `rejected` closes the review loop: a human who declines a proposal needs an
#: outcome distinct from "archived" (retired after being accepted) and from
#: "proposed" (still awaiting review).
MEMORY_STATUSES = {"proposed", "accepted", "rejected", "archived", "superseded"}

#: Statuses that may be injected as authoritative project context. Exactly one.
AUTHORITATIVE_STATUSES = frozenset({"accepted"})

MAX_INJECTION_BYTES = 20_000
MAX_INJECTION_ITEMS = 5

#: Upper bound on rows pulled out of SQL for scoring. The SQL prefilter already
#: narrows to memories matching at least one query term, so this only bites on a
#: project where hundreds of memories match the same term; those are taken
#: most-recent-first. Keeps retrieval O(bounded) rather than O(project history) —
#: SQLite FTS5 is the documented upgrade path if this ever becomes the limit.
CANDIDATE_LIMIT = 200


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

    # ------------------------------------------------------------------ create

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
        source_commit: str | None = None,
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
                source_commit=source_commit,
            )
            session.add(mem)
            await session.commit()
            await session.refresh(mem)
            await self._emit_event(event_types.MEMORY_CREATED, mem)
            return mem

    async def propose_from_execution(
        self,
        project_id: int,
        type: str,
        title: str,
        content: str,
        source_role: str,
        source_task_id: int | None = None,
        source_execution_id: str | None = None,
        source_commit: str | None = None,
        tags: list[str] | None = None,
    ) -> ProjectMemory:
        """Record agent output as a *proposal*, never as project truth.

        The status is not a parameter. An agent's conclusion is speculative until
        a human accepts it, and the only way to make it authoritative is
        `accept()`, which is an explicit human action. Keeping `status` out of the
        signature means no caller can promote speculation by passing an argument.
        """
        return await self.create(
            project_id=project_id,
            type=type,
            title=title,
            content=content,
            status="proposed",
            tags=tags,
            source=source_role,
            source_task_id=source_task_id,
            source_execution_id=source_execution_id,
            source_commit=source_commit,
        )

    async def get(self, memory_id: int) -> ProjectMemory | None:
        async with self._session_factory() as session:
            return await session.get(ProjectMemory, memory_id)

    # ------------------------------------------------------------------ update

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

    # --------------------------------------------------------------- lifecycle

    async def accept(self, memory_id: int, actor: str = "founder") -> ProjectMemory | None:
        """Promote a proposal to authoritative project truth. Human action only."""
        return await self._transition(
            memory_id, "accepted", actor, event_types.MEMORY_ACCEPTED,
        )

    async def reject(
        self, memory_id: int, actor: str = "founder", reason: str = "",
    ) -> ProjectMemory | None:
        """Decline a proposal. It stays on record with its provenance."""
        return await self._transition(
            memory_id, "rejected", actor, event_types.MEMORY_REJECTED, reason=reason,
        )

    async def archive(self, memory_id: int) -> ProjectMemory | None:
        return await self._transition(
            memory_id, "archived", "founder", event_types.MEMORY_ARCHIVED,
        )

    async def _transition(
        self,
        memory_id: int,
        status: str,
        actor: str,
        event_type: str,
        reason: str = "",
    ) -> ProjectMemory | None:
        async with self._session_factory() as session:
            mem = await session.get(ProjectMemory, memory_id)
            if mem is None:
                return None
            previous = mem.status
            mem.status = status
            await session.commit()
            await session.refresh(mem)
            # Review provenance lives in the event log: who acted, when, and on
            # what it was before.
            await self._emit_event(
                event_type, mem, extra={
                    "from": previous, "to": status, "actor": actor,
                    **({"reason": reason} if reason else {}),
                },
            )
            return mem

    async def supersede(
        self, memory_id: int, replacement_id: int,
    ) -> tuple[ProjectMemory | None, ProjectMemory | None]:
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
            await self._emit_event(event_types.MEMORY_SUPERSEDED, old, extra={
                "superseded_by": replacement.id,
            })
            return old, replacement

    # ------------------------------------------------------------------ search

    async def search(
        self,
        project_id: int,
        query: str = "",
        types: Sequence[str] | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[ProjectMemory]:
        """Browse/search for the UI. Ordered by relevance when a query is given.

        Unlike `get_relevant`, this is not restricted to accepted memories: a
        human reviewing proposals needs to find them.
        """
        rows = await self._fetch_candidates(
            project_id,
            query=query,
            types=types,
            statuses=[status] if status else None,
            tags=tags,
            limit=max(limit, CANDIDATE_LIMIT) if query.strip() else limit,
        )
        if not query.strip():
            return rows[:limit]

        matches = rank(_to_candidates(rows), query, limit=limit)
        by_id = {row.id: row for row in rows}
        return [by_id[m.memory_id] for m in matches]

    # --------------------------------------------------------------- retrieval

    async def get_relevant_matches(
        self,
        project_id: int,
        query: str = "",
        types: Sequence[str] | None = None,
        limit: int = MAX_INJECTION_ITEMS,
        statuses: Sequence[str] = tuple(AUTHORITATIVE_STATUSES),
    ) -> list[tuple[ProjectMemory, MemoryMatch]]:
        """Scored retrieval, pairing each memory with the reason it matched.

        With an empty query there is nothing to score, so the most recently
        updated memories are returned with an explicit `recency_only` signal —
        never a fabricated relevance score.
        """
        rows = await self._fetch_candidates(
            project_id,
            query=query,
            types=types,
            statuses=list(statuses),
            limit=CANDIDATE_LIMIT,
        )
        if not rows:
            return []

        if not tokenize(query):
            return [
                (
                    row,
                    MemoryMatch(
                        memory_id=row.id,
                        score=0.0,
                        matched_terms=(),
                        matched_tags=(),
                        type=row.type,
                        status=row.status,
                        source=row.source,
                        signals={"recency_only": 1.0},
                        coverage=0.0,
                    ),
                )
                for row in rows[:limit]
            ]

        matches = rank(_to_candidates(rows), query, limit=limit)
        by_id = {row.id: row for row in rows}
        return [(by_id[m.memory_id], m) for m in matches]

    async def get_relevant(
        self,
        project_id: int,
        query: str = "",
        types: Sequence[str] | None = None,
        limit: int = MAX_INJECTION_ITEMS,
    ) -> list[ProjectMemory]:
        """Accepted memories relevant to a query, most relevant first."""
        return [
            row
            for row, _ in await self.get_relevant_matches(
                project_id, query=query, types=types, limit=limit,
            )
        ]

    async def injection_context(
        self,
        project_id: int,
        task_description: str,
        types: Sequence[str] | None = None,
        include_proposed: bool = True,
    ) -> dict:
        """Bounded, explainable context for workflow nodes.

        Returns:
            memories        accepted items, authoritative, each with `retrieval`
                            metadata saying why it was selected
            proposed        matching proposals, for display only — never
                            presented to an agent as settled project truth
            query_terms     the terms retrieval actually searched for
            total_bytes     encoded size of the authoritative items
            truncated       whether the byte budget cut the list short
            injected_ids    ids of the authoritative items

        `proposed` is a separate key rather than a flag on each item so a caller
        cannot accidentally splice speculation into the authoritative block: the
        prompt builder concatenates `memories`, and `proposed` is simply not
        reachable from there.
        """
        accepted = await self.get_relevant_matches(
            project_id, query=task_description, types=types,
        )

        result_items: list[dict] = []
        total = 0
        truncated = False
        for mem, match in accepted:
            entry = _entry(mem, match)
            entry_bytes = len(json.dumps(entry, default=str).encode("utf-8"))
            if total + entry_bytes > MAX_INJECTION_BYTES:
                truncated = True
                break
            result_items.append(entry)
            total += entry_bytes

        proposed_items: list[dict] = []
        if include_proposed:
            proposals = await self.get_relevant_matches(
                project_id,
                query=task_description,
                types=types,
                statuses=("proposed",),
            )
            proposed_items = [_entry(mem, match) for mem, match in proposals]

        return {
            "memories": result_items,
            "proposed": proposed_items,
            "query_terms": tokenize(task_description),
            "total_bytes": total,
            "truncated": truncated,
            "injected_ids": [item["id"] for item in result_items],
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

    # ----------------------------------------------------------------- fetching

    async def _fetch_candidates(
        self,
        project_id: int,
        query: str = "",
        types: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        tags: list[str] | None = None,
        limit: int = CANDIDATE_LIMIT,
    ) -> list[ProjectMemory]:
        """Narrow to plausible candidates in SQL; scoring happens in Python.

        The prefilter is an OR over *individual terms* — the whole point of the
        fix. One `ILIKE '%<entire task description>%'` matched nothing, because
        no memory contains a task description verbatim.
        """
        conditions = [ProjectMemory.project_id == project_id]

        if statuses:
            conditions.append(ProjectMemory.status.in_(list(statuses)))
        if types:
            conditions.append(ProjectMemory.type.in_(list(types)))
        if tags:
            conditions.append(
                or_(*[ProjectMemory.tags.contains(json.dumps(t)) for t in tags])
            )

        terms = tokenize(query)
        if terms:
            # Cast the JSON tags column to text so a term can match a tag here
            # too; precise tag matching is Python's job.
            tags_as_text = cast(ProjectMemory.tags, Text)
            term_clauses = []
            for term in terms:
                pattern = f"%{term}%"
                term_clauses.extend([
                    ProjectMemory.title.ilike(pattern),
                    ProjectMemory.content.ilike(pattern),
                    tags_as_text.ilike(pattern),
                ])
            conditions.append(or_(*term_clauses))

        stmt = (
            select(ProjectMemory)
            .where(and_(*conditions))
            .order_by(ProjectMemory.updated_at.desc(), ProjectMemory.id.desc())
            .limit(limit)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ------------------------------------------------------------------- events

    async def _emit_event(
        self, event_type: str, mem: ProjectMemory, extra: dict | None = None,
    ) -> None:
        payload = {
            "id": mem.id,
            "project_id": mem.project_id,
            "type": mem.type,
            "title": mem.title,
            "status": mem.status,
            "source": mem.source,
            **(extra or {}),
        }
        row = await self._events.append(
            execution_id=mem.source_execution_id,
            task_id=mem.source_task_id,
            type=event_type,
            payload=payload,
        )
        # Publish the persisted row id: SSE clients de-duplicate on it, so a
        # hard-coded 0 made every memory event after the first look like a
        # replay of the first and get dropped.
        await self._bus.publish({
            "id": row.id,
            "execution_id": mem.source_execution_id,
            "task_id": mem.source_task_id,
            "type": event_type,
            "payload": payload,
            "severity": "info",
            "timestamp": row.timestamp.isoformat(),
        })


def _to_candidates(rows: Sequence[ProjectMemory]) -> list[Candidate]:
    """Adapt ORM rows to the pure scoring value object.

    `recency` is the row's position in the already-recency-ordered fetch,
    inverted, so newer rows win ties without scoring needing a clock.
    """
    total = len(rows)
    return [
        Candidate(
            id=row.id,
            type=row.type,
            title=row.title or "",
            content=row.content or "",
            tags=tuple(row.tags or ()),
            status=row.status,
            source=row.source,
            recency=float(total - index),
        )
        for index, row in enumerate(rows)
    ]


def _entry(mem: ProjectMemory, match: MemoryMatch) -> dict:
    """One memory as injected/displayed, carrying its selection rationale."""
    return {
        "id": mem.id,
        "type": mem.type,
        "title": mem.title,
        "content": mem.content,
        "tags": list(mem.tags or ()),
        "status": mem.status,
        "source": mem.source,
        "source_task_id": mem.source_task_id,
        "source_execution_id": mem.source_execution_id,
        "source_commit": mem.source_commit,
        "created_at": mem.created_at.isoformat() if mem.created_at else None,
        # Why this item is here. Without it an operator cannot tell a relevant
        # injection from an accidental one.
        "retrieval": match.as_dict(),
    }
