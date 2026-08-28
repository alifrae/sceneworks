"""Durable, provider-neutral evidence ledger for EngineeringSessions (WP15)."""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engineering_models import EngineeringEvidence, EngineeringSession, EngineeringTurn
from app.models import Task

_MAX_PAYLOAD_STRING = 120_000


class EngineeringEvidenceError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_payload(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= _MAX_PAYLOAD_STRING:
            return value
        return value[:_MAX_PAYLOAD_STRING] + "\n[truncated by SceneWorks evidence limit]"
    if isinstance(value, dict):
        return {str(key): _bounded_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded_payload(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _sanitize_payload(category: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist evidence, not a second copy of repository source/diffs."""
    cleaned = dict(payload)
    if category.strip().lower() == "git":
        for key in ("committed", "working", "staged"):
            value = cleaned.pop(key, None)
            if isinstance(value, str):
                cleaned[f"{key}_sha256"] = hashlib.sha256(value.encode("utf-8")).hexdigest()
                cleaned[f"{key}_chars"] = len(value)
    return cleaned


class EngineeringEvidenceService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def begin_turn(self, session_id: int, intent: str = "") -> EngineeringTurn:
        async with self._session_factory() as session:
            engineering = await session.get(EngineeringSession, session_id)
            if engineering is None:
                raise EngineeringEvidenceError(f"engineering session {session_id} not found")
            if engineering.status != "ACTIVE":
                raise EngineeringEvidenceError(
                    f"engineering session {session_id} is {engineering.status.lower()}, not active"
                )
            active = (
                await session.execute(
                    select(EngineeringTurn)
                    .where(
                        EngineeringTurn.engineering_session_id == session_id,
                        EngineeringTurn.status == "ACTIVE",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if active is not None:
                raise EngineeringEvidenceError(
                    f"engineering session {session_id} already has active turn {active.id}; finish it before starting another"
                )
            turn = EngineeringTurn(
                id=uuid.uuid4().hex,
                engineering_session_id=session_id,
                task_id=engineering.task_id,
                intent=intent.strip(),
                status="ACTIVE",
            )
            session.add(turn)
            await session.commit()
            await session.refresh(turn)
            return turn

    async def finish_turn(
        self, session_id: int, turn_id: str, status: str = "COMPLETED"
    ) -> EngineeringTurn:
        normalized = status.strip().upper()
        if normalized not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise EngineeringEvidenceError(
                "turn status must be COMPLETED, FAILED or CANCELLED"
            )
        async with self._session_factory() as session:
            turn = await session.get(EngineeringTurn, turn_id)
            if turn is None or turn.engineering_session_id != session_id:
                raise EngineeringEvidenceError(
                    "turn does not belong to this engineering session"
                )
            if turn.status != "ACTIVE":
                return turn
            turn.status = normalized
            turn.finished_at = _now()
            turn.updated_at = _now()
            await session.commit()
            await session.refresh(turn)
            return turn

    async def validate_turn(self, session_id: int, turn_id: str | None) -> str | None:
        if not turn_id:
            return None
        async with self._session_factory() as session:
            turn = await session.get(EngineeringTurn, turn_id)
            if turn is None or turn.engineering_session_id != session_id:
                raise EngineeringEvidenceError(
                    "turn does not belong to this engineering session"
                )
            if turn.status != "ACTIVE":
                raise EngineeringEvidenceError(
                    f"turn {turn_id} is {turn.status.lower()}, not active"
                )
            return turn.id

    async def record(
        self,
        session_id: int,
        *,
        category: str,
        operation: str,
        status: str,
        payload: dict[str, Any] | None = None,
        turn_id: str | None = None,
        action_id: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> EngineeringEvidence:
        action_id = action_id or uuid.uuid4().hex
        category = category.strip().lower()
        async with self._session_factory() as session:
            engineering = await session.get(EngineeringSession, session_id)
            if engineering is None:
                raise EngineeringEvidenceError(
                    f"engineering session {session_id} not found"
                )
            if turn_id:
                turn = await session.get(EngineeringTurn, turn_id)
                if turn is None or turn.engineering_session_id != session_id:
                    raise EngineeringEvidenceError(
                        "turn does not belong to this engineering session"
                    )
            row = EngineeringEvidence(
                engineering_session_id=session_id,
                task_id=engineering.task_id,
                turn_id=turn_id,
                action_id=action_id,
                category=category,
                operation=operation.strip(),
                status=status.strip().upper(),
                started_at=started_at or _now(),
                finished_at=finished_at or _now(),
                payload=_bounded_payload(_sanitize_payload(category, payload or {})),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_turns(
        self, session_id: int, limit: int = 50
    ) -> list[EngineeringTurn]:
        async with self._session_factory() as session:
            engineering = await session.get(EngineeringSession, session_id)
            if engineering is None:
                raise EngineeringEvidenceError(
                    f"engineering session {session_id} not found"
                )
            return list(
                (
                    await session.execute(
                        select(EngineeringTurn)
                        .where(EngineeringTurn.engineering_session_id == session_id)
                        .order_by(EngineeringTurn.created_at.desc())
                        .limit(max(1, min(limit, 200)))
                    )
                ).scalars().all()
            )

    async def list_evidence(
        self,
        session_id: int,
        *,
        turn_id: str | None = None,
        category: str | None = None,
        after_id: int | None = None,
        limit: int = 100,
    ) -> list[EngineeringEvidence]:
        async with self._session_factory() as session:
            engineering = await session.get(EngineeringSession, session_id)
            if engineering is None:
                raise EngineeringEvidenceError(
                    f"engineering session {session_id} not found"
                )
            query = select(EngineeringEvidence).where(
                EngineeringEvidence.engineering_session_id == session_id
            )
            if turn_id:
                query = query.where(EngineeringEvidence.turn_id == turn_id)
            if category:
                query = query.where(
                    EngineeringEvidence.category == category.strip().lower()
                )
            if after_id is not None:
                query = query.where(EngineeringEvidence.id > after_id)
            query = query.order_by(EngineeringEvidence.id.asc()).limit(
                max(1, min(limit, 500))
            )
            return list((await session.execute(query)).scalars().all())

    async def summary(self, session_id: int) -> dict[str, Any]:
        async with self._session_factory() as session:
            engineering = await session.get(EngineeringSession, session_id)
            if engineering is None:
                raise EngineeringEvidenceError(
                    f"engineering session {session_id} not found"
                )
            task = (
                await session.get(Task, engineering.task_id)
                if engineering.task_id is not None
                else None
            )
            rows = list(
                (
                    await session.execute(
                        select(EngineeringEvidence)
                        .where(
                            EngineeringEvidence.engineering_session_id == session_id
                        )
                        .order_by(EngineeringEvidence.id.asc())
                    )
                ).scalars().all()
            )
            turn_count = int(
                (
                    await session.execute(
                        select(func.count(EngineeringTurn.id)).where(
                            EngineeringTurn.engineering_session_id == session_id
                        )
                    )
                ).scalar_one()
            )

        categories = Counter(row.category for row in rows)
        statuses = Counter(row.status for row in rows)
        non_failures = {
            "COMPLETED",
            "SUCCEEDED",
            "RUNNING",
            "STARTED",
            "QUEUED",
        }
        failures = [row for row in rows if row.status not in non_failures]
        latest_by_category: dict[str, EngineeringEvidence] = {}
        for row in rows:
            latest_by_category[row.category] = row
        latest_git = latest_by_category.get("git")
        changed_files = []
        if latest_git is not None:
            raw_changed = (latest_git.payload or {}).get("changed_files")
            if isinstance(raw_changed, list):
                changed_files = raw_changed
        contract = dict(task.engineering_contract or {}) if task is not None else {}
        return {
            "engineering_session_id": session_id,
            "project_id": engineering.project_id,
            "task_id": engineering.task_id,
            "task": (
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "acceptance_criteria": list(contract.get("acceptance_criteria") or []),
                    "required_tests": list(contract.get("required_tests") or []),
                }
                if task is not None
                else None
            ),
            "base_commit": engineering.base_commit,
            "branch": engineering.branch,
            "turn_count": turn_count,
            "evidence_count": len(rows),
            "categories": dict(sorted(categories.items())),
            "statuses": dict(sorted(statuses.items())),
            "failure_count": len(failures),
            "latest_evidence_id": rows[-1].id if rows else None,
            "changed_files": changed_files,
            "latest_actions": [
                {
                    "id": row.id,
                    "turn_id": row.turn_id,
                    "action_id": row.action_id,
                    "category": row.category,
                    "operation": row.operation,
                    "status": row.status,
                }
                for row in rows[-12:]
            ],
            "latest_failures": [evidence_row(row) for row in failures[-10:]],
        }


def turn_row(row: EngineeringTurn) -> dict[str, Any]:
    return {
        "id": row.id,
        "engineering_session_id": row.engineering_session_id,
        "task_id": row.task_id,
        "intent": row.intent,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def evidence_row(row: EngineeringEvidence) -> dict[str, Any]:
    return {
        "id": row.id,
        "engineering_session_id": row.engineering_session_id,
        "task_id": row.task_id,
        "turn_id": row.turn_id,
        "action_id": row.action_id,
        "category": row.category,
        "operation": row.operation,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "payload": dict(row.payload or {}),
    }
