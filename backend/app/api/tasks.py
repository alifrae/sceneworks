"""Task resource routes: CRUD + workflow actions + diff/provenance views."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import get_context
from app.context import AppContext
from app.domain.task_states import TaskStateMachine, TaskStatus
from app.models import Execution, Initiative, Project, Task, WorkPackage
from app.schemas import (
    ActionRequest,
    CapabilityProfile,
    DiffOut,
    EngineeringContract,
    TaskBacklogUpdate,
    TaskCreate,
    TaskOut,
    TaskProvenanceOut,
)
from app.services.attachments import delete_task_tree
from app.services.workflow import WorkflowError

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

ACTIONS = {
    "start-architecture": "start_architecture",
    "approve-architecture": "approve_architecture",
    "reject-architecture": "reject_architecture",
    "request-architecture-revision": "request_architecture_revision",
    "start-implementation": "start_implementation",
    "start-review": "start_review",
    "accept": "accept",
    "reject": "reject",
    "send-back": "send_back_to_engineer",
    "cancel": "cancel",
    "retry": "retry",
    "cleanup-worktree": "cleanup_worktree",
}


async def _task_out(ctx: AppContext, task: Task) -> TaskOut:
    async with ctx.engine_factory() as session:
        project = await session.get(Project, task.project_id)
        execution = None
        if task.current_execution_id:
            execution = await session.get(Execution, task.current_execution_id)
    out = TaskOut.model_validate(task)
    out.project_name = project.name if project else ""
    out.allowed_actions = TaskStateMachine.allowed_actions(TaskStatus(task.status))
    out.execution_status = execution.status if execution else None
    return out


async def _validate_work_package(session, project_id: int, work_package_id: int | None) -> None:
    if work_package_id is None:
        return
    work_package = await session.get(WorkPackage, work_package_id)
    if work_package is None:
        raise HTTPException(404, "work package not found")
    initiative = await session.get(Initiative, work_package.initiative_id)
    if initiative is None or initiative.project_id != project_id:
        raise HTTPException(400, "work package belongs to a different project")


@router.get("")
async def list_tasks(
    project_id: int | None = None,
    status: str | None = None,
    role: str | None = None,
    work_item_type: str | None = None,
    mode: str | None = None,
    priority: str | None = None,
    query: str | None = None,
    limit: int = 200,
    ctx: AppContext = Depends(get_context),
) -> list[TaskOut]:
    async with ctx.engine_factory() as session:
        stmt = select(Task).options(selectinload(Task.project)).order_by(Task.updated_at.desc()).limit(min(limit, 500))
        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        if status:
            stmt = stmt.where(Task.status == status.upper())
        if role:
            stmt = stmt.where(Task.current_role == role)
        if work_item_type:
            stmt = stmt.where(Task.work_item_type == work_item_type.lower())
        if priority:
            stmt = stmt.where(Task.priority == priority.lower())
        if mode:
            normalized = mode.lower()
            if normalized == "auto":
                stmt = stmt.where(Task.requested_mode == "auto")
            else:
                stmt = stmt.where(
                    or_(
                        Task.resolved_mode == normalized,
                        (Task.resolved_mode.is_(None)) & (Task.requested_mode == normalized),
                    )
                )
        if query and query.strip():
            needle = f"%{query.strip()}%"
            stmt = stmt.where(or_(Task.title.ilike(needle), Task.description.ilike(needle)))
        tasks = (await session.execute(stmt)).scalars().all()
        execution_ids = [t.current_execution_id for t in tasks if t.current_execution_id]
        execution_status: dict[str, str] = {}
        if execution_ids:
            rows = (
                await session.execute(
                    select(Execution.id, Execution.status).where(Execution.id.in_(execution_ids))
                )
            ).all()
            execution_status = {execution_id: row_status for execution_id, row_status in rows}

    result: list[TaskOut] = []
    for task in tasks:
        out = TaskOut.model_validate(task)
        out.project_name = task.project.name if task.project else ""
        out.allowed_actions = TaskStateMachine.allowed_actions(TaskStatus(task.status))
        out.execution_status = execution_status.get(task.current_execution_id or "")
        result.append(out)
    return result


@router.post("", status_code=201)
async def create_task(body: TaskCreate, ctx: AppContext = Depends(get_context)) -> TaskOut:
    async with ctx.engine_factory() as session:
        project = await session.get(Project, body.project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        await _validate_work_package(session, body.project_id, body.work_package_id)

        task = Task(
            project_id=body.project_id,
            work_package_id=body.work_package_id,
            title=body.title,
            description=body.description,
            priority=body.priority,
            work_item_type=body.work_item_type,
            requested_mode=body.requested_mode,
            resolved_mode=None if body.requested_mode == "auto" else body.requested_mode,
            engineering_contract=body.engineering_contract.model_dump(),
            capability_requirements=body.capability_requirements.model_dump(),
            status=TaskStatus.NEW.value,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
    return await _task_out(ctx, task)


@router.get("/{task_id}")
async def get_task(task_id: int, ctx: AppContext = Depends(get_context)) -> TaskOut:
    async with ctx.engine_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "task not found")
    return await _task_out(ctx, task)


@router.patch("/{task_id}")
async def update_backlog_task(
    task_id: int,
    body: TaskBacklogUpdate,
    ctx: AppContext = Depends(get_context),
) -> TaskOut:
    """Edit lightweight work-management metadata before execution starts."""
    async with ctx.engine_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        if task.status != TaskStatus.NEW.value:
            raise HTTPException(409, "task metadata can only be changed while task is NEW")
        data = body.model_dump(exclude_unset=True)
        if "work_package_id" in data:
            await _validate_work_package(session, task.project_id, data["work_package_id"])
        for key, value in data.items():
            setattr(task, key, value)
        if "requested_mode" in data:
            task.resolved_mode = None if task.requested_mode == "auto" else task.requested_mode
        await session.commit()
        await session.refresh(task)
    return await _task_out(ctx, task)


@router.put("/{task_id}/contract")
async def replace_engineering_contract(
    task_id: int,
    body: EngineeringContract,
    ctx: AppContext = Depends(get_context),
) -> TaskOut:
    """Replace the task's binding contract before workflow execution starts."""
    async with ctx.engine_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        if task.status != TaskStatus.NEW.value:
            raise HTTPException(
                409,
                "engineering contract can only be changed while task is NEW; "
                "create/revise the contract before starting architecture",
            )
        task.engineering_contract = body.model_dump()
        await session.commit()
        await session.refresh(task)
    return await _task_out(ctx, task)


@router.put("/{task_id}/capabilities")
async def replace_capability_requirements(
    task_id: int,
    body: CapabilityProfile,
    ctx: AppContext = Depends(get_context),
) -> TaskOut:
    """Replace task capability overlays before workflow execution starts."""
    async with ctx.engine_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        if task.status != TaskStatus.NEW.value:
            raise HTTPException(409, "capability requirements can only be changed while task is NEW")
        task.capability_requirements = body.model_dump()
        await session.commit()
        await session.refresh(task)
    return await _task_out(ctx, task)


@router.get("/{task_id}/provenance")
async def task_provenance(
    task_id: int, ctx: AppContext = Depends(get_context)
) -> TaskProvenanceOut:
    row = await ctx.provenance.task(task_id)
    if row is None:
        raise HTTPException(404, "task not found")
    return TaskProvenanceOut(**row)


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: int, ctx: AppContext = Depends(get_context)) -> None:
    project_id: int | None = None
    async with ctx.engine_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        if task.status not in (TaskStatus.NEW.value, TaskStatus.CANCELLED.value, TaskStatus.REJECTED.value):
            raise HTTPException(409, "only NEW, CANCELLED or REJECTED tasks can be deleted")
        execution_count = (
            await session.execute(select(func.count(Execution.id)).where(Execution.task_id == task_id))
        ).scalar() or 0
        if execution_count:
            raise HTTPException(409, "task has execution history; cannot delete")
        project_id = task.project_id
        await session.delete(task)
        await session.commit()
    if project_id is not None:
        delete_task_tree(ctx.settings, project_id, task_id)


@router.get("/{task_id}/events")
async def task_events(
    task_id: int, after_id: int | None = None, limit: int = 500,
    ctx: AppContext = Depends(get_context)
):
    rows = await ctx.event_store.list_for_task(task_id, after_id=after_id, limit=min(limit, 800))
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


@router.get("/{task_id}/diff")
async def task_diff(task_id: int, ctx: AppContext = Depends(get_context)) -> DiffOut:
    async with ctx.engine_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "task not found")
    if not task.worktree_path or not Path(task.worktree_path).is_dir():
        return DiffOut(error="no worktree exists for this task yet")
    base = task.base_commit or task.result_commit
    if not base:
        return DiffOut(error="no base commit recorded")
    try:
        diff = await ctx.git.diff(Path(task.worktree_path), base)
        commits = await ctx.git.list_commits(Path(task.worktree_path), base)
        status = await ctx.git.status(Path(task.worktree_path))
    except Exception as exc:  # noqa: BLE001
        return DiffOut(error=str(exc))
    return DiffOut(stat=diff["stat"], full=diff["full"], commits=commits, status=status)


@router.post("/{task_id}/actions/{action}")
async def task_action(
    task_id: int,
    action: str,
    body: ActionRequest | None = None,
    ctx: AppContext = Depends(get_context),
) -> TaskOut:
    body = body or ActionRequest()
    wm = ctx.workflow_manager
    try:
        if action == "start-architecture":
            await wm.start_workflow(task_id)
        elif action == "approve-architecture":
            await wm.resume_approval(task_id, "approve")
        elif action == "reject-architecture":
            await wm.resume_approval(task_id, "reject", body.reason)
        elif action == "request-architecture-revision":
            await wm.resume_approval(task_id, "revision", body.notes)
        elif action == "start-implementation":
            await wm.start_implementation(task_id)
        elif action == "start-review":
            await wm.start_review(task_id)
        elif action == "accept":
            await ctx.provenance.capture_task_changes(task_id)
            await wm.accept(task_id)
        elif action == "reject":
            await ctx.provenance.capture_task_changes(task_id)
            await wm.reject(task_id, body.reason)
        elif action == "send-back":
            await wm.send_back_to_engineer(task_id, body.notes)
        elif action == "cancel":
            await wm.cancel(task_id)
        elif action == "retry":
            await wm.retry(task_id)
        elif action == "cleanup-worktree":
            await ctx.provenance.capture_task_changes(task_id)
            await wm.cleanup_worktree(task_id)
        else:
            raise HTTPException(404, f"unknown action: {action}")
    except WorkflowError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    async with ctx.engine_factory() as session:
        task = await session.get(Task, task_id)
    return await _task_out(ctx, task)
