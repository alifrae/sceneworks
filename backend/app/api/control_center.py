"""Read-only aggregate for the WP20 web engineering control center.

This endpoint intentionally exposes operational metadata only. Mutating engineering
operations stay in the existing governed task/MCP/runtime surfaces so the web UI
does not become a second authority path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import get_context
from app.context import AppContext
from app.engineering_models import EngineeringEvidence, EngineeringSession
from app.models import Project, Task
from app.pcs_models import PcsRun

router = APIRouter(prefix="/api/control-center", tags=["control-center"])

ATTENTION_STATES = {
    "AWAITING_ARCHITECTURE_APPROVAL",
    "CHANGES_REQUESTED",
    "READY_FOR_HUMAN",
    "FAILED",
}
ACTIVE_TASK_STATES = {
    "NEW",
    "TRIAGING",
    "ARCHITECTURE_ANALYSIS",
    "AWAITING_ARCHITECTURE_APPROVAL",
    "READY_TO_IMPLEMENT",
    "IMPLEMENTING",
    "TESTING",
    "REVIEWING",
    "CHANGES_REQUESTED",
    "READY_FOR_HUMAN",
}
CLOSED_STATES = {"ACCEPTED", "REJECTED", "CANCELLED"}
ISSUE_TYPES = {"bug", "feature", "idea"}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("")
async def control_center(ctx: AppContext = Depends(get_context)) -> dict:
    """Return a bounded operational snapshot for the web control center."""
    async with ctx.engine_factory() as session:
        project_count = int(
            (await session.execute(select(func.count(Project.id)))).scalar() or 0
        )
        active_task_count = int(
            (
                await session.execute(
                    select(func.count(Task.id)).where(Task.status.in_(ACTIVE_TASK_STATES))
                )
            ).scalar()
            or 0
        )
        attention_count = int(
            (
                await session.execute(
                    select(func.count(Task.id)).where(Task.status.in_(ATTENTION_STATES))
                )
            ).scalar()
            or 0
        )
        open_issue_count = int(
            (
                await session.execute(
                    select(func.count(Task.id)).where(
                        Task.work_item_type.in_(ISSUE_TYPES),
                        Task.status.not_in(CLOSED_STATES),
                    )
                )
            ).scalar()
            or 0
        )
        closed_issue_count = int(
            (
                await session.execute(
                    select(func.count(Task.id)).where(
                        Task.work_item_type.in_(ISSUE_TYPES),
                        Task.status.in_(CLOSED_STATES),
                    )
                )
            ).scalar()
            or 0
        )

        session_rows = (
            await session.execute(
                select(EngineeringSession, Project.name, Task.title)
                .join(Project, EngineeringSession.project_id == Project.id)
                .outerjoin(Task, EngineeringSession.task_id == Task.id)
                .order_by(EngineeringSession.updated_at.desc())
                .limit(20)
            )
        ).all()

        pcs_rows = (
            await session.execute(
                select(PcsRun, Project.name, Task.title)
                .join(Project, PcsRun.project_id == Project.id)
                .outerjoin(Task, PcsRun.task_id == Task.id)
                .order_by(PcsRun.updated_at.desc())
                .limit(20)
            )
        ).all()

        evidence_rows = (
            await session.execute(
                select(EngineeringEvidence, Project.name, Task.title)
                .join(
                    EngineeringSession,
                    EngineeringEvidence.engineering_session_id == EngineeringSession.id,
                )
                .join(Project, EngineeringSession.project_id == Project.id)
                .outerjoin(Task, EngineeringEvidence.task_id == Task.id)
                .order_by(EngineeringEvidence.created_at.desc())
                .limit(30)
            )
        ).all()

    return {
        "projects": project_count,
        "tasks": {
            "active": active_task_count,
            "needs_attention": attention_count,
        },
        "issues": {
            "open": open_issue_count,
            "closed": closed_issue_count,
        },
        "engineering_sessions": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "project_name": project_name,
                "task_id": row.task_id,
                "task_title": task_title,
                "runtime": row.runtime,
                "status": row.status,
                "branch": row.branch,
                "permissions": list(row.permissions or []),
                "updated_at": _iso(row.updated_at),
            }
            for row, project_name, task_title in session_rows
        ],
        "pcs_runs": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "project_name": project_name,
                "engineering_session_id": row.engineering_session_id,
                "task_id": row.task_id,
                "task_title": task_title,
                "profile": row.profile_name,
                "status": row.status,
                "pid": row.pid,
                "exit_code": row.exit_code,
                "updated_at": _iso(row.updated_at),
            }
            for row, project_name, task_title in pcs_rows
        ],
        "recent_evidence": [
            {
                "id": row.id,
                "engineering_session_id": row.engineering_session_id,
                "project_name": project_name,
                "task_id": row.task_id,
                "task_title": task_title,
                "turn_id": row.turn_id,
                "action_id": row.action_id,
                "category": row.category,
                "operation": row.operation,
                "status": row.status,
                "created_at": _iso(row.created_at),
            }
            for row, project_name, task_title in evidence_rows
        ],
    }
