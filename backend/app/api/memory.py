"""Project Memory API routes (V2.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_context
from app.context import AppContext
from app.schemas import (
    MemoryCreate,
    MemoryOut,
    MemorySearchParams,
    MemoryUpdate,
    VALID_MEMORY_TYPES,
)

router = APIRouter(prefix="/api/projects/{project_id}/memory", tags=["memory"])


def _mem_out(mem) -> MemoryOut:
    return MemoryOut.model_validate(mem)


@router.post("", response_model=MemoryOut, status_code=201)
async def create_memory(
    project_id: int,
    body: MemoryCreate,
    ctx: AppContext = Depends(get_context),
):
    if body.type not in VALID_MEMORY_TYPES:
        raise HTTPException(400, f"invalid type: {body.type!r}")
    try:
        mem = await ctx.memory.create(
            project_id=project_id,
            type=body.type,
            title=body.title,
            content=body.content,
            status=body.status,
            tags=body.tags,
            source=body.source,
            source_task_id=body.source_task_id,
            source_execution_id=body.source_execution_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _mem_out(mem)


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    project_id: int,
    types: list[str] | None = Query(None),
    status: str | None = None,
    tags: list[str] | None = Query(None),
    query: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
):
    return [
        _mem_out(m)
        for m in await ctx.memory.search(
            project_id=project_id,
            query=query,
            types=types,
            status=status,
            tags=tags,
            limit=limit,
        )
    ]


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    project_id: int,
    memory_id: int,
    ctx: AppContext = Depends(get_context),
):
    mem = await ctx.memory.get(memory_id)
    if mem is None or mem.project_id != project_id:
        raise HTTPException(404, "memory not found")
    return _mem_out(mem)


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    project_id: int,
    memory_id: int,
    body: MemoryUpdate,
    ctx: AppContext = Depends(get_context),
):
    mem = await ctx.memory.get(memory_id)
    if mem is None or mem.project_id != project_id:
        raise HTTPException(404, "memory not found")
    if body.type is not None and body.type not in VALID_MEMORY_TYPES:
        raise HTTPException(400, f"invalid type: {body.type!r}")
    try:
        updated = await ctx.memory.update(
            memory_id=memory_id,
            type=body.type,
            title=body.title,
            content=body.content,
            status=body.status,
            tags=body.tags,
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _mem_out(updated)


@router.post("/{memory_id}/archive", response_model=MemoryOut)
async def archive_memory(
    project_id: int,
    memory_id: int,
    ctx: AppContext = Depends(get_context),
):
    mem = await ctx.memory.get(memory_id)
    if mem is None or mem.project_id != project_id:
        raise HTTPException(404, "memory not found")
    updated = await ctx.memory.archive(memory_id)
    return _mem_out(updated)


@router.post("/{memory_id}/supersede", response_model=dict)
async def supersede_memory(
    project_id: int,
    memory_id: int,
    replacement_id: int = Query(description="ID of the replacement memory item"),
    ctx: AppContext = Depends(get_context),
):
    mem = await ctx.memory.get(memory_id)
    if mem is None or mem.project_id != project_id:
        raise HTTPException(404, "memory not found")
    old, replacement = await ctx.memory.supersede(memory_id, replacement_id)
    if replacement is None:
        raise HTTPException(404, "replacement memory not found")
    return {
        "superseded": _mem_out(old),
        "replacement": _mem_out(replacement),
    }
