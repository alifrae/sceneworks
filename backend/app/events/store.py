"""Durable event persistence (SQLite)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Event


class EventStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def append(
        self,
        *,
        execution_id: str | None,
        task_id: int | None,
        type: str,
        payload: dict,
        severity: str = "info",
        session: AsyncSession | None = None,
    ) -> Event:
        """Persist an event.

        Pass `session` to enlist the event in a caller-owned transaction so
        that a state change and the event describing it commit together. The
        caller is then responsible for committing; the row is flushed so its
        id and timestamp are populated.
        """
        row = Event(
            execution_id=execution_id,
            task_id=task_id,
            type=type,
            payload=payload,
            severity=severity,
        )
        if session is not None:
            session.add(row)
            await session.flush()
            return row
        async with self._session_factory() as own_session:
            own_session.add(row)
            await own_session.commit()
            await own_session.refresh(row)
            return row

    async def list_for_task(
        self, task_id: int, *, after_id: int | None = None, limit: int = 500
    ) -> list[Event]:
        async with self._session_factory() as session:
            stmt = select(Event).where(Event.task_id == task_id)
            if after_id is not None:
                stmt = stmt.where(Event.id > after_id)
            stmt = stmt.order_by(Event.id.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return list(reversed(rows))

    async def list_for_execution(
        self, execution_id: str, *, after_id: int | None = None, limit: int = 500
    ) -> list[Event]:
        async with self._session_factory() as session:
            stmt = select(Event).where(Event.execution_id == execution_id)
            if after_id is not None:
                stmt = stmt.where(Event.id > after_id)
            stmt = stmt.order_by(Event.id.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return list(reversed(rows))
