"""Server-Sent Events streaming for live task/execution updates."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_context
from app.context import AppContext

router = APIRouter(prefix="/api/events", tags=["events"])

HEARTBEAT_INTERVAL = 15.0


def _sse_payload(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


async def _stream(
    ctx: AppContext,
    task_id: int | None,
    execution_id: str | None,
) -> StreamingResponse:
    async def generator():
        seen: set[int] = set()

        # Replay persisted history first (subscribe after replay start, dedupe).
        queue = await ctx.bus.register()
        try:
            rows = []
            if execution_id:
                rows = await ctx.event_store.list_for_execution(
                    execution_id, limit=ctx.settings.sse_replay_events
                )
            elif task_id is not None:
                rows = await ctx.event_store.list_for_task(
                    task_id, limit=ctx.settings.sse_replay_events
                )
            for row in rows:
                if (task_id is not None and row.task_id != task_id) or (
                    execution_id is not None and row.execution_id != execution_id
                ):
                    continue
                event_id = row.id
                seen.add(event_id)
                yield _sse_payload(
                    {
                        "id": event_id,
                        "execution_id": row.execution_id,
                        "task_id": row.task_id,
                        "type": row.type,
                        "payload": row.payload,
                        "severity": row.severity,
                        "timestamp": row.timestamp.isoformat(),
                    }
                )

            # Live stream with heartbeat.
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                event_id = event.get("id")
                if event_id is not None and event_id in seen:
                    continue
                if event_id is not None:
                    seen.add(event_id)
                if task_id is not None and event.get("task_id") != task_id:
                    continue
                if execution_id is not None and event.get("execution_id") != execution_id:
                    continue
                yield _sse_payload(event)
        finally:
            await ctx.bus.unregister(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stream")
async def stream_events(
    task_id: int | None = None,
    execution_id: str | None = None,
    ctx: AppContext = Depends(get_context),
):
    return await _stream(ctx, task_id, execution_id)
