"""Dashboard: operational overview, no fake KPIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from app.api.deps import get_context
from app.context import AppContext
from app.models import Execution, Task
from app.schemas import DashboardOut, ExecutionOut, TaskOut

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(ctx: AppContext = Depends(get_context)) -> DashboardOut:
    async with ctx.engine_factory() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        active_tasks = (
            await session.execute(
                select(func.count(Task.id)).where(
                    Task.status.in_(["ARCHITECTURE_ANALYSIS", "IMPLEMENTING", "REVIEWING"])
                )
            )
        ).scalar() or 0
        awaiting = (
            await session.execute(
                select(func.count(Task.id)).where(
                    Task.status.in_(["AWAITING_ARCHITECTURE_APPROVAL", "READY_FOR_HUMAN"])
                )
            )
        ).scalar() or 0
        running = (
            await session.execute(
                select(func.count(Execution.id)).where(
                    Execution.status.in_(["QUEUED", "STARTING", "RUNNING"])
                )
            )
        ).scalar() or 0
        recent_tasks = (
            await session.execute(
                select(Task)
                .options(selectinload(Task.project))
                .where(Task.status.in_(["READY_FOR_HUMAN", "ACCEPTED", "REJECTED"]))
                .order_by(Task.updated_at.desc())
                .limit(8)
            )
        ).scalars().all()
        failed_rows = (
            await session.execute(
                select(Execution)
                .where(Execution.status.in_(["FAILED", "INTERRUPTED"]))
                .order_by(Execution.finished_at.desc().nulls_last())
                .limit(8)
            )
        ).scalars().all()
        await session.commit()

    task_outs = []
    for t in recent_tasks:
        out = TaskOut.model_validate(t)
        out.project_name = t.project.name if t.project else ""
        task_outs.append(out)
    roles = [
        {"key": r.key, "display_name": r.display_name, "backend": r.backend}
        for r in ctx.roles.all()
    ]
    return DashboardOut(
        active_tasks=int(active_tasks),
        awaiting_approval=int(awaiting),
        running_executions=int(running),
        recently_completed=task_outs,
        failed_executions=[ExecutionOut.model_validate(r) for r in failed_rows],
        roles=roles,
    )
