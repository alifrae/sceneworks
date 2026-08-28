"""Task attachment routes.

Attachments are immutable task context. They can be added or removed only
while a task is NEW, before any workflow execution can consume them.  The
transport uses bounded base64 JSON rather than multipart so the local API keeps
its dependency surface small and MCP/browser callers can share the same model.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_context
from app.context import AppContext
from app.models import Task, TaskAttachment
from app.services.attachments import (
    AttachmentError,
    delete_file,
    read_bytes,
    resolve_storage_key,
    store_bytes,
)

router = APIRouter(prefix="/api/tasks", tags=["attachments"])


class TaskAttachmentCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    data_base64: str = Field(min_length=1)
    source: str = "web"


class TaskAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    source: str
    created_at: datetime


async def _new_task(session, task_id: int) -> Task:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.status != "NEW":
        raise HTTPException(
            409,
            "attachments are frozen once task execution starts; add or remove them while task is NEW",
        )
    return task


async def _quota(session, task_id: int) -> tuple[int, int]:
    row = (
        await session.execute(
            select(
                func.count(TaskAttachment.id),
                func.coalesce(func.sum(TaskAttachment.size_bytes), 0),
            ).where(TaskAttachment.task_id == task_id)
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def decode_attachment_payload(value: str, max_bytes: int) -> bytes:
    """Decode strict base64 while bounding both encoded and decoded input."""
    # Base64 expands bytes by ~4/3. A small allowance covers padding/newline-free
    # encoding without letting a hostile JSON body allocate unbounded memory.
    max_encoded = ((max_bytes + 2) // 3) * 4 + 8
    if len(value) > max_encoded:
        raise HTTPException(413, f"attachment exceeds {max_bytes} byte limit")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "attachment data_base64 is not valid base64") from exc
    if len(data) > max_bytes:
        raise HTTPException(413, f"attachment exceeds {max_bytes} byte limit")
    return data


@router.get("/{task_id}/attachments")
async def list_task_attachments(
    task_id: int, ctx: AppContext = Depends(get_context)
) -> list[TaskAttachmentOut]:
    async with ctx.engine_factory() as session:
        if await session.get(Task, task_id) is None:
            raise HTTPException(404, "task not found")
        rows = list(
            (
                await session.execute(
                    select(TaskAttachment)
                    .where(TaskAttachment.task_id == task_id)
                    .order_by(TaskAttachment.created_at.asc())
                )
            ).scalars().all()
        )
    return [TaskAttachmentOut.model_validate(row) for row in rows]


@router.post("/{task_id}/attachments", status_code=201)
async def upload_task_attachment(
    task_id: int,
    body: TaskAttachmentCreate,
    ctx: AppContext = Depends(get_context),
) -> TaskAttachmentOut:
    source = body.source.strip().lower() or "web"
    if source not in {"web", "api", "mcp"}:
        raise HTTPException(400, "invalid attachment source")
    data = decode_attachment_payload(body.data_base64, ctx.settings.attachment_max_bytes)

    async with ctx.engine_factory() as session:
        task = await _new_task(session, task_id)
        count, total = await _quota(session, task_id)
        if count >= ctx.settings.attachment_task_max_count:
            raise HTTPException(
                413,
                f"task already has the maximum {ctx.settings.attachment_task_max_count} attachments",
            )
        if total + len(data) > ctx.settings.attachment_task_max_bytes:
            raise HTTPException(
                413,
                f"task attachments exceed {ctx.settings.attachment_task_max_bytes} byte total limit",
            )

        try:
            stored = store_bytes(
                ctx.settings,
                project_id=task.project_id,
                task_id=task_id,
                filename=body.filename,
                data=data,
            )
        except AttachmentError as exc:
            raise HTTPException(400, str(exc)) from exc

        row = TaskAttachment(
            task_id=task_id,
            filename=stored.filename,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            source=source,
        )
        session.add(row)
        try:
            await session.commit()
            await session.refresh(row)
        except IntegrityError as exc:
            await session.rollback()
            delete_file(ctx.settings, stored.storage_key)
            raise HTTPException(
                409, "this task already contains the same attachment content"
            ) from exc
        except Exception:
            await session.rollback()
            delete_file(ctx.settings, stored.storage_key)
            raise
    return TaskAttachmentOut.model_validate(row)


@router.get("/{task_id}/attachments/{attachment_id}/content")
async def task_attachment_content(
    task_id: int,
    attachment_id: int,
    download: bool = False,
    ctx: AppContext = Depends(get_context),
):
    async with ctx.engine_factory() as session:
        row = await session.get(TaskAttachment, attachment_id)
        if row is None or row.task_id != task_id:
            raise HTTPException(404, "attachment not found")
    try:
        path = resolve_storage_key(ctx.settings, row.storage_key)
        # Read once to detect missing content before FileResponse starts headers.
        read_bytes(ctx.settings, row.storage_key)
    except AttachmentError as exc:
        raise HTTPException(410, str(exc)) from exc
    disposition = "attachment" if download else "inline"
    return FileResponse(
        Path(path),
        media_type=row.mime_type,
        filename=row.filename,
        content_disposition_type=disposition,
    )


@router.delete("/{task_id}/attachments/{attachment_id}", status_code=204)
async def delete_task_attachment(
    task_id: int,
    attachment_id: int,
    ctx: AppContext = Depends(get_context),
) -> None:
    storage_key: str | None = None
    async with ctx.engine_factory() as session:
        await _new_task(session, task_id)
        row = await session.get(TaskAttachment, attachment_id)
        if row is None or row.task_id != task_id:
            raise HTTPException(404, "attachment not found")
        storage_key = row.storage_key
        await session.delete(row)
        await session.commit()
    if storage_key:
        delete_file(ctx.settings, storage_key)
