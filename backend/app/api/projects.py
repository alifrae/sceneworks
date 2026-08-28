"""Project resource routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, func, or_, select

from app.api.deps import get_context
from app.context import AppContext
from app.git.workspace import GitError
from app.models import (
    AgentSession,
    Artifact,
    Event,
    Execution,
    Initiative,
    Project,
    ProjectMemory,
    Task,
    TaskAttachment,
    WorkPackage,
)
from app.schemas import (
    ProjectCreate,
    ProjectOut,
    ProjectProvenanceOut,
    ProjectUpdate,
    RepoStatusOut,
    TaskProvenanceOut,
)
from app.services.attachments import delete_project_tree

router = APIRouter(prefix="/api/projects", tags=["projects"])

ACTIVE_TASK_STATES = {
    "NEW",
    "TRIAGING",
    "ARCHITECTURE_ANALYSIS",
    "AWAITING_ARCHITECTURE_APPROVAL",
    "READY_TO_IMPLEMENT",
    "IMPLEMENTING",
    "TESTING",
    "REVIEWING",
    "CHANGES_REQUESTED",
    "READY_FOR_HUMAN",
}
ACTIVE_SESSION_STATES = {"STARTING", "READY", "RUNNING"}


class ProjectPolicyBody(BaseModel):
    """Long-lived engineering constraints that apply to every project task."""

    protected_paths: list[str] = Field(default_factory=list)
    architecture_invariants: list[str] = Field(default_factory=list)
    forbidden_dependency_directions: list[str] = Field(default_factory=list)
    documentation_requirements: list[str] = Field(default_factory=list)
    performance_constraints: list[str] = Field(default_factory=list)
    required_review_checks: list[str] = Field(default_factory=list)
    go_no_go_commands: list[str] = Field(default_factory=list)
    release_requirements: list[str] = Field(default_factory=list)
    policy_file_paths: list[str] = Field(default_factory=list)


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
async def list_projects(limit: int = 200, ctx: AppContext = Depends(get_context)) -> list[ProjectOut]:
    async with ctx.engine_factory() as session:
        projects = (
            await session.execute(
                select(Project).order_by(Project.created_at.desc()).limit(min(limit, 500))
            )
        ).scalars().all()
        project_ids = [project.id for project in projects]
        active_counts: dict[int, int] = {}
        if project_ids:
            rows = (
                await session.execute(
                    select(Task.project_id, func.count(Task.id))
                    .where(
                        Task.project_id.in_(project_ids),
                        Task.status.in_(["NEW", "ARCHITECTURE_ANALYSIS", "READY_TO_IMPLEMENT", "IMPLEMENTING", "TESTING", "REVIEWING", "CHANGES_REQUESTED"]),
                    )
                    .group_by(Task.project_id)
                )
            ).all()
            active_counts = {project_id: int(count) for project_id, count in rows}
    return [
        ProjectOut.model_validate(project).model_copy(update={"active_task_count": active_counts.get(project.id, 0)})
        for project in projects
    ]


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
            capability_profile=body.capability_profile.model_dump(),
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


@router.get("/{project_id}/policy", response_model=ProjectPolicyBody)
async def get_project_policy(
    project_id: int, ctx: AppContext = Depends(get_context)
) -> ProjectPolicyBody:
    """Return an always-answerable project policy; unconfigured means empty."""
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        return ProjectPolicyBody.model_validate(project.engineering_policy or {})


@router.put("/{project_id}/policy", response_model=ProjectPolicyBody)
async def put_project_policy(
    project_id: int,
    body: ProjectPolicyBody,
    ctx: AppContext = Depends(get_context),
) -> ProjectPolicyBody:
    """Fully replace a project's engineering policy."""
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        project.engineering_policy = body.model_dump()
        await session.commit()
        await session.refresh(project)
        return ProjectPolicyBody.model_validate(project.engineering_policy or {})


@router.delete("/{project_id}/policy", status_code=204)
async def delete_project_policy(
    project_id: int, ctx: AppContext = Depends(get_context)
) -> None:
    """Clear project policy without deleting the project."""
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        project.engineering_policy = {}
        await session.commit()


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


@router.get("/{project_id}/provenance")
async def project_provenance(
    project_id: int,
    path: str | None = None,
    limit: int = 100,
    ctx: AppContext = Depends(get_context),
) -> ProjectProvenanceOut:
    """Answer which previous SceneWorks tasks changed a given repository path.

    When ``path`` is omitted the endpoint returns recent persisted provenance for
    the project. Results survive worktree cleanup because changed paths are
    captured from Git when implementation completes.
    """
    async with ctx.engine_factory() as session:
        if await session.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")
    rows = await ctx.provenance.project_history(project_id, path=path, limit=limit)
    return ProjectProvenanceOut(
        project_id=project_id,
        path=path.strip().replace("\\", "/") if path and path.strip() else None,
        tasks=[TaskProvenanceOut(**row) for row in rows],
    )


async def _purge_project_history(session, project_id: int) -> None:
    """Delete SceneWorks-owned records for a project, never repository files."""
    task_ids = list(
        (await session.execute(select(Task.id).where(Task.project_id == project_id))).scalars()
    )
    execution_ids: list[str] = []
    if task_ids:
        execution_ids = list(
            (
                await session.execute(
                    select(Execution.id).where(Execution.task_id.in_(task_ids))
                )
            ).scalars()
        )

    initiative_ids = list(
        (
            await session.execute(
                select(Initiative.id).where(Initiative.project_id == project_id)
            )
        ).scalars()
    )
    work_package_ids: list[int] = []
    if initiative_ids:
        work_package_ids = list(
            (
                await session.execute(
                    select(WorkPackage.id).where(
                        WorkPackage.initiative_id.in_(initiative_ids)
                    )
                )
            ).scalars()
        )

    event_filters = []
    if task_ids:
        event_filters.append(Event.task_id.in_(task_ids))
    if execution_ids:
        event_filters.append(Event.execution_id.in_(execution_ids))
    if event_filters:
        await session.execute(sa_delete(Event).where(or_(*event_filters)))

    await session.execute(
        sa_delete(ProjectMemory).where(ProjectMemory.project_id == project_id)
    )
    await session.execute(sa_delete(Artifact).where(Artifact.project_id == project_id))
    await session.execute(
        sa_delete(AgentSession).where(AgentSession.project_id == project_id)
    )
    if execution_ids:
        await session.execute(
            sa_delete(Execution).where(Execution.id.in_(execution_ids))
        )
    if task_ids:
        await session.execute(
            sa_delete(TaskAttachment).where(TaskAttachment.task_id.in_(task_ids))
        )
        await session.execute(sa_delete(Task).where(Task.id.in_(task_ids)))
    if work_package_ids:
        await session.execute(
            sa_delete(WorkPackage).where(WorkPackage.id.in_(work_package_ids))
        )
    if initiative_ids:
        await session.execute(
            sa_delete(Initiative).where(Initiative.id.in_(initiative_ids))
        )
    await session.execute(sa_delete(Project).where(Project.id == project_id))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    purge_history: bool = False,
    force: bool = False,
    ctx: AppContext = Depends(get_context),
) -> None:
    """Unregister a project without ever touching its Git repository.

    By default deletion is allowed only when no SceneWorks-owned history exists.
    ``purge_history=true`` also deletes terminal project history. Active tasks or
    agent sessions still block deletion unless ``force=true`` is explicitly
    supplied; force is intended for deterministic test-artifact cleanup.
    """
    purged = False
    async with ctx.engine_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "project not found")

        active_task_count = (
            await session.execute(
                select(func.count(Task.id)).where(
                    Task.project_id == project_id,
                    Task.status.in_(ACTIVE_TASK_STATES),
                )
            )
        ).scalar() or 0
        active_session_count = (
            await session.execute(
                select(func.count(AgentSession.id)).where(
                    AgentSession.project_id == project_id,
                    AgentSession.status.in_(ACTIVE_SESSION_STATES),
                )
            )
        ).scalar() or 0
        if not force and (active_task_count or active_session_count):
            raise HTTPException(
                409,
                "cannot delete project while it has active tasks or agent sessions",
            )

        dependent_counts = [
            (
                await session.execute(
                    select(func.count(Task.id)).where(Task.project_id == project_id)
                )
            ).scalar()
            or 0,
            (
                await session.execute(
                    select(func.count(Initiative.id)).where(
                        Initiative.project_id == project_id
                    )
                )
            ).scalar()
            or 0,
            (
                await session.execute(
                    select(func.count(ProjectMemory.id)).where(
                        ProjectMemory.project_id == project_id
                    )
                )
            ).scalar()
            or 0,
            (
                await session.execute(
                    select(func.count(Artifact.id)).where(Artifact.project_id == project_id)
                )
            ).scalar()
            or 0,
            (
                await session.execute(
                    select(func.count(AgentSession.id)).where(
                        AgentSession.project_id == project_id
                    )
                )
            ).scalar()
            or 0,
        ]
        if any(dependent_counts) and not purge_history:
            raise HTTPException(
                409,
                "project has SceneWorks history; retry with purge_history=true to delete it",
            )

        if purge_history:
            await _purge_project_history(session, project_id)
            purged = True
        else:
            await session.delete(project)
        await session.commit()
    if purged:
        delete_project_tree(ctx.settings, project_id)
