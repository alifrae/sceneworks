"""Project resource routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.api.deps import get_context
from app.context import AppContext
from app.git.workspace import GitError
from app.models import Project, Task
from app.schemas import ProjectCreate, ProjectOut, ProjectUpdate, RepoStatusOut

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _to_out(ctx: AppContext, project: Project) -> ProjectOut:
    async with ctx.engine_factory() as session:
        active_count = (
            await session.execute(
                select(func.count(Task.id)).where(
                    Task.project_id == project.id,
                    Task.status.in_(["NEW", "ARCHITECTURE_ANALYSIS", "READY_TO_IMPLEMENT", "IMPLEMENTING", "TESTING", "REVIEWING", "CHANGES_REQUESTED"]),
                )
            )
        ).scalar() or 0
    out = ProjectOut.model_validate(project)
    out.active_task_count = int(active_count)
    return out


@router.get("")
async def list_projects(ctx: AppContext = Depends(get_context)) -> list[ProjectOut]:
    async with ctx.engine_factory() as session:
        projects = (await session.execute(select(Project).order_by(Project.created_at.desc()))).scalars().all()
    return [await _to_out(ctx, p) for p in projects]


@router.post("", status_code=201)
async def create_project(body: ProjectCreate, ctx: AppContext = Depends(get_context)) -> ProjectOut:
    path = Path(body.repository_path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(400, f"repository path does not exist: {path}")
    info = await ctx.git.repo_info(path)
    if not info.is_git:
        raise HTTPException(400, f"not a Git repository: {path} ({info.error})")
    default_branch = body.default_branch or info.head_branch or ""
    async with ctx.engine_factory() as session:
        existing = (await session.execute(select(Project).where(Project.repository_path == str(path)))).scalar_one_or_none()
        if existing:
            raise HTTPException(409, f"project already registered for repository {path}")
        project = Project(
            name=body.name,
            description=body.description,
            repository_path=str(path),
            default_branch=default_branch,
            architecture_context_paths=body.architecture_context_paths,
            test_commands=body.test_commands,
            build_commands=body.build_commands,
            worktree_root_override=body.worktree_root_override,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
    return await _to_out(ctx, project)


@router.get("/{project_id}")
async def get_project(project_id: int, ctx: AppContext = Depends(get_context)) -> ProjectOut:
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
    return await _to_out(ctx, project)


@router.patch("/{project_id}")
async def update_project(
    project_id: int, body: ProjectUpdate, ctx: AppContext = Depends(get_context)
) -> ProjectOut:
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        data = body.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(project, key, value)
        await session.commit()
        await session.refresh(project)
    return await _to_out(ctx, project)


@router.get("/{project_id}/status")
async def project_status(project_id: int, ctx: AppContext = Depends(get_context)) -> RepoStatusOut:
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        active_tasks = (
            await session.execute(
                select(func.count(Task.id)).where(
                    Task.project_id == project_id,
                    Task.status.in_(["ARCHITECTURE_ANALYSIS", "IMPLEMENTING", "REVIEWING"]),
                )
            )
        ).scalar() or 0
    info = await ctx.git.repo_info(Path(project.repository_path))
    worktrees: list[dict] = []
    if info.is_git:
        try:
            worktrees = await ctx.git.worktree_list(Path(project.repository_path))
        except GitError as exc:
            info.error = str(exc)
    return RepoStatusOut(
        is_git=info.is_git,
        head_branch=info.head_branch,
        head_commit=info.head_commit,
        error=info.error,
        worktrees=worktrees,
        active_tasks=int(active_tasks),
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int, ctx: AppContext = Depends(get_context)) -> None:
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        task_count = (
            await session.execute(select(func.count(Task.id)).where(Task.project_id == project_id))
        ).scalar() or 0
        if task_count:
            raise HTTPException(409, "cannot delete project with existing tasks")
        await session.delete(project)
        await session.commit()
