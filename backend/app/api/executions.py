"""Execution resource routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_context
from app.context import AppContext
from app.models import Execution
from app.schemas import ExecutionOut

router = APIRouter(prefix="/api/executions", tags=["executions"])


@router.get("")
async def list_executions(
    task_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
    ctx: AppContext = Depends(get_context),
) -> list[ExecutionOut]:
    async with ctx.engine_factory() as session:
        stmt = select(Execution).order_by(Execution.created_at.desc()).limit(min(limit, 500))
        if task_id is not None:
            stmt = stmt.where(Execution.task_id == task_id)
        if status:
            stmt = stmt.where(Execution.status == status.upper())
        rows = (await session.execute(stmt)).scalars().all()
    return [ExecutionOut.model_validate(r) for r in rows]


@router.get("/{execution_id}")
async def get_execution(
    execution_id: str, ctx: AppContext = Depends(get_context)
) -> ExecutionOut:
    async with ctx.engine_factory() as session:
        row = await session.get(Execution, execution_id)
        if row is None:
            raise HTTPException(404, "execution not found")
    return ExecutionOut.model_validate(row)


@router.get("/{execution_id}/events")
async def execution_events(
    execution_id: str,
    after_id: int | None = None,
    limit: int = 500,
    ctx: AppContext = Depends(get_context),
):
    rows = await ctx.event_store.list_for_execution(execution_id, after_id=after_id, limit=min(limit, 800))
    return [
        {
            "id": r.id,
            "execution_id": r.execution_id,
            "task_id": r.task_id,
            "type": r.type,
            "payload": r.payload,
            "severity": r.severity,
            "timestamp": r.timestamp.isoformat(),
        }
        for r in rows
    ]


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str, ctx: AppContext = Depends(get_context)
) -> dict:
    cancelled = await ctx.execution_engine.cancel(execution_id)
    if not cancelled:
        async with ctx.engine_factory() as session:
            row = await session.get(Execution, execution_id)
        if row is None:
            raise HTTPException(404, "execution not found")
        raise HTTPException(409, "execution is not running")
    return {"cancelled": True}
