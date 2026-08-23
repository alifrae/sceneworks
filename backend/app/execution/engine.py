"""Execution engine.

Runs agent executions as asyncio tasks, persists lifecycle events, supports
cancellation with process cleanup, and reconciles interrupted work on startup.
The engine is generic: workflows create immutable Execution rows; the engine
executes the backend/model already resolved on those rows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import AgentBackend, AgentEventSink, AgentRequest, AgentResult, Workspace
from app.agents.registry import BackendRegistry
from app.config.settings import Settings
from app.events import types as event_types
from app.events.bus import EventBus
from app.events.store import EventStore
from app.models import Execution, Task

logger = logging.getLogger("sceneworks.execution")

ACTIVE_STATUSES = {"QUEUED", "STARTING", "RUNNING"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
RUNNING_TASK_STATES = {"ARCHITECTURE_ANALYSIS", "IMPLEMENTING", "REVIEWING"}
ResultStatus = Callable[[str], Awaitable[None]]


@dataclass
class ActiveRun:
    task: asyncio.Task
    sink: AgentEventSink
    backend_key: str


class ExecutionNotFoundError(KeyError):
    pass


class ExecutionEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        event_store: EventStore,
        backends: BackendRegistry,
        settings: Settings,
    ):
        self._session_factory = session_factory
        self._bus = bus
        self._event_store = event_store
        self._backends = backends
        self._settings = settings
        self._active: dict[str, ActiveRun] = {}
        self.on_execution_finished: ResultStatus | None = None
        self._shutting_down = False

    async def start(self, execution_id: str) -> None:
        if execution_id in self._active:
            return
        async with self._session_factory() as session:
            row = await session.get(Execution, execution_id)
            if row is None:
                raise ExecutionNotFoundError(execution_id)
            if row.status in ("STARTING", "RUNNING"):
                return
            row.status = "QUEUED"
            await session.commit()
            task_id = row.task_id
            backend_key = row.backend
        if execution_id in self._active:
            return
        sink = AgentEventSink(execution_id, task_id, self._emit)
        run = ActiveRun(task=None, sink=sink, backend_key=backend_key)
        self._active[execution_id] = run
        run.task = asyncio.create_task(
            self._execute(execution_id, sink), name=f"exec-{execution_id}"
        )

    async def cancel(self, execution_id: str) -> bool:
        run = self._active.get(execution_id)
        if run is None:
            return False
        run.sink.cancel()
        try:
            await self._backends.get(run.backend_key).cancel(execution_id)
        except Exception:  # noqa: BLE001
            logger.exception("backend cancel failed for %s", execution_id)
        asyncio.create_task(self._force_cancel_after_grace(execution_id))
        return True

    async def fail_before_start(self, execution_id: str, error: str) -> None:
        await self._finalize(
            execution_id,
            status="FAILED",
            error=error,
            event_type=event_types.EXECUTION_FAILED,
            event_payload={"error": error},
            event_severity="error",
        )

    async def _force_cancel_after_grace(self, execution_id: str) -> None:
        await asyncio.sleep(self._settings.cancel_grace_seconds)
        run = self._active.get(execution_id)
        if run is None or run.task.done():
            return
        logger.warning("forcing cancellation of execution %s", execution_id)
        run.task.cancel()
        try:
            await run.task
        except asyncio.CancelledError:
            pass
        await self._finalize(
            execution_id,
            status="CANCELLED",
            error="cancelled (forced after grace period)",
            event_type=event_types.EXECUTION_CANCELLED,
            event_payload={"reason": "forced cancellation"},
            event_severity="warning",
        )
        await self._notify_finished(execution_id)

    async def shutdown(self) -> None:
        self._shutting_down = True
        for execution_id, run in list(self._active.items()):
            run.sink.cancel()
            try:
                await self._backends.get(run.backend_key).cancel(execution_id)
            except Exception:  # noqa: BLE001
                pass
            run.task.cancel()
            try:
                await run.task
            except asyncio.CancelledError:
                pass
            await self._finalize(
                execution_id,
                status="INTERRUPTED",
                error="interrupted by SceneWorks shutdown",
            )
        self._active.clear()

    async def _execute(self, execution_id: str, sink: AgentEventSink) -> None:
        try:
            async with self._session_factory() as session:
                row = await self._load_execution(session, execution_id)
                row.status = "STARTING"
                row.started_at = datetime.now(timezone.utc)
                await session.commit()
                snapshot = {
                    "role": row.role,
                    "backend": row.backend,
                    "model_profile": row.model_profile,
                    "model": row.model_name,
                    "task_id": row.task_id,
                    "workspace": dict(row.workspace or {}),
                    "system_prompt": row.system_prompt,
                    "user_prompt": row.user_prompt,
                }
            await self._emit(
                event_types.EXECUTION_STARTED,
                {
                    "role": snapshot["role"],
                    "backend": snapshot["backend"],
                    "model_profile": snapshot["model_profile"],
                    "model": snapshot["model"],
                },
                execution_id=execution_id,
            )
            cwd = snapshot["workspace"].get("cwd") or snapshot["workspace"].get("repo_path")
            workspace = Workspace(
                path=str(cwd or Path.cwd()),
                repo_path=str(snapshot["workspace"].get("repo_path") or ""),
                branch=snapshot["workspace"].get("branch"),
                base_commit=snapshot["workspace"].get("base_commit"),
                permissions=tuple(snapshot["workspace"].get("permissions") or ()),
            )
            request = AgentRequest(
                execution_id=execution_id,
                role=snapshot["role"],
                system_prompt=snapshot["system_prompt"] or "",
                user_prompt=snapshot["user_prompt"] or "",
                model_profile=snapshot["model_profile"],
                model=snapshot["model"],
                metadata={"task_id": snapshot["task_id"]},
            )
            backend: AgentBackend = self._backends.get(snapshot["backend"])
            timeout = self._settings.execution_timeout_seconds + 60
            try:
                result = await asyncio.wait_for(
                    backend.run(request, workspace, sink), timeout=timeout
                )
            except asyncio.TimeoutError:
                result = AgentResult(
                    status="failed",
                    error=f"execution exceeded {timeout}s (engine hard timeout)",
                )
            except asyncio.CancelledError:
                result = AgentResult(status="cancelled", error="execution cancelled")
            except Exception as exc:  # noqa: BLE001
                logger.exception("backend run crashed for %s", execution_id)
                result = AgentResult(status="failed", error=f"{type(exc).__name__}: {exc}")

            cancelled_status = "INTERRUPTED" if self._shutting_down else "CANCELLED"
            cancelled_event = (
                event_types.EXECUTION_INTERRUPTED
                if self._shutting_down
                else event_types.EXECUTION_CANCELLED
            )
            status_map = {
                "completed": "COMPLETED",
                "cancelled": cancelled_status,
                "failed": "FAILED",
            }
            final_event = {
                "completed": event_types.EXECUTION_COMPLETED,
                "cancelled": cancelled_event,
                "failed": event_types.EXECUTION_FAILED,
            }[result.status]
            if result.status == "cancelled" and self._shutting_down:
                result = AgentResult(
                    status="cancelled",
                    error="interrupted by SceneWorks shutdown; check logs before retrying",
                )
            await self._finalize(
                execution_id,
                status=status_map[result.status],
                result=result.summary,
                error=result.error,
                event_type=final_event,
                event_payload={
                    "error": result.error,
                    "summary": (result.summary or "")[:2000],
                },
                event_severity="error" if result.status == "failed" else "info",
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("execution %s crashed in engine", execution_id)
            await self._finalize(
                execution_id, status="FAILED", error="engine failure (see logs)"
            )
        finally:
            self._active.pop(execution_id, None)
            await self._notify_finished(execution_id)

    async def _notify_finished(self, execution_id: str) -> None:
        hook = self.on_execution_finished
        if hook is None:
            return
        try:
            await hook(execution_id)
        except Exception:  # noqa: BLE001
            logger.exception("workflow continuation failed for %s", execution_id)
            await self._mark_task_failed_on_continuation_error(execution_id)

    async def _mark_task_failed_on_continuation_error(self, execution_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(Execution, execution_id)
            if row is None or row.task_id is None:
                return
            task = await session.get(Task, row.task_id)
            if task is not None and task.status not in {"ACCEPTED", "REJECTED", "CANCELLED"}:
                task.status = "FAILED"
                await session.commit()

    async def _load_execution(self, session: AsyncSession, execution_id: str) -> Execution:
        row = await session.get(Execution, execution_id)
        if row is None:
            raise ExecutionNotFoundError(execution_id)
        return row

    async def _emit(
        self,
        type: str,
        payload: dict,
        severity: str = "info",
        *,
        execution_id: str | None = None,
    ) -> None:
        task_id: int | None = None
        if execution_id:
            async with self._session_factory() as session:
                row = await session.get(Execution, execution_id)
                if row is not None:
                    task_id = row.task_id
        row = await self._event_store.append(
            execution_id=execution_id,
            task_id=task_id,
            type=type,
            payload=payload,
            severity=severity,
        )
        await self._bus.publish(
            {
                "id": row.id,
                "execution_id": execution_id,
                "task_id": task_id,
                "type": type,
                "payload": payload,
                "severity": severity,
                "timestamp": row.timestamp.isoformat(),
            }
        )

    async def _finalize(
        self,
        execution_id: str,
        *,
        status: str,
        result: str | None = None,
        error: str | None = None,
        event_type: str | None = None,
        event_payload: dict | None = None,
        event_severity: str = "info",
    ) -> None:
        published: dict | None = None
        async with self._session_factory() as session:
            row = await session.get(Execution, execution_id)
            if row is None or row.status in TERMINAL_STATUSES:
                return
            row.status = status
            if result is not None:
                row.result = result
            if error is not None:
                row.error = error
            row.finished_at = datetime.now(timezone.utc)
            task_id = row.task_id
            if event_type is not None:
                event_row = await self._event_store.append(
                    execution_id=execution_id,
                    task_id=task_id,
                    type=event_type,
                    payload=event_payload or {},
                    severity=event_severity,
                    session=session,
                )
                published = {
                    "id": event_row.id,
                    "execution_id": execution_id,
                    "task_id": task_id,
                    "type": event_type,
                    "payload": event_payload or {},
                    "severity": event_severity,
                    "timestamp": event_row.timestamp.isoformat(),
                }
            await session.commit()
        if published is not None:
            await self._bus.publish(published)

    async def recover_interrupted(self) -> list[str]:
        interrupted: list[str] = []
        failed_task_ids: list[int] = []
        async with self._session_factory() as session:
            rows = (
                (await session.execute(select(Execution).where(Execution.status.in_(ACTIVE_STATUSES))))
                .scalars()
                .all()
            )
            for row in rows:
                row.status = "INTERRUPTED"
                row.error = "interrupted by SceneWorks restart; check logs before retrying"
                row.finished_at = datetime.now(timezone.utc)
                interrupted.append(row.id)
                if row.task_id is not None:
                    task = await session.get(Task, row.task_id)
                    if task is not None and task.status in RUNNING_TASK_STATES:
                        task.status = "FAILED"
                        task.updated_at = datetime.now(timezone.utc)
                        failed_task_ids.append(task.id)
            await session.commit()

            stranded = (
                (await session.execute(
                    select(Task).where(Task.status.in_(sorted(RUNNING_TASK_STATES)))
                ))
                .scalars()
                .all()
            )
            for task in stranded:
                if task.id in failed_task_ids:
                    continue
                still_active = (
                    await session.execute(
                        select(Execution.id)
                        .where(Execution.task_id == task.id)
                        .where(Execution.status.in_(ACTIVE_STATUSES))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if still_active is not None:
                    continue
                task.status = "FAILED"
                task.current_role = None
                task.current_execution_id = None
                task.updated_at = datetime.now(timezone.utc)
                failed_task_ids.append(task.id)
                logger.warning(
                    "task %s was left in a running state with no active execution; marked FAILED so it can be retried",
                    task.id,
                )
            await session.commit()
        for execution_id in interrupted:
            await self._emit(
                event_types.EXECUTION_INTERRUPTED,
                {"reason": "SceneWorks restart"},
                severity="warning",
                execution_id=execution_id,
            )
        for task_id in failed_task_ids:
            await self._event_store.append(
                execution_id=None,
                task_id=task_id,
                type=event_types.TASK_TRANSITIONED,
                payload={
                    "from": "active",
                    "to": "FAILED",
                    "action": "recovery",
                    "actor": "system",
                },
            )
        return interrupted

    def active_ids(self) -> list[str]:
        return list(self._active.keys())

    def is_active(self, execution_id: str) -> bool:
        return execution_id in self._active
