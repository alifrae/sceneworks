"""Task verification projection over existing SceneWorks evidence."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_context
from app.context import AppContext
from app.services.verification import TaskVerificationError

router = APIRouter(prefix="/api/tasks", tags=["verification"])


@router.get("/{task_id}/verification")
async def task_verification(
    task_id: int,
    ctx: AppContext = Depends(get_context),
) -> dict:
    try:
        return await ctx.verification.view(task_id)
    except TaskVerificationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
