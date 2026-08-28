"""Project-scoped PCS runtime-control configuration API (WP16)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_context
from app.context import AppContext
from app.pcs_schemas import PcsRuntimeControlConfig
from app.services.pcs_control import PcsControlError

router = APIRouter(prefix="/api/projects", tags=["pcs-control"])


@router.get("/{project_id}/pcs-control", response_model=PcsRuntimeControlConfig)
async def get_pcs_control(
    project_id: int, ctx: AppContext = Depends(get_context)
) -> PcsRuntimeControlConfig:
    try:
        return await ctx.pcs_control.get_config(project_id)
    except PcsControlError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.put("/{project_id}/pcs-control", response_model=PcsRuntimeControlConfig)
async def put_pcs_control(
    project_id: int,
    body: PcsRuntimeControlConfig,
    ctx: AppContext = Depends(get_context),
) -> PcsRuntimeControlConfig:
    try:
        return await ctx.pcs_control.set_config(project_id, body)
    except PcsControlError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(status, detail=str(exc)) from exc
