"""WP5 initiative and work-package planning API.

This layer owns durable decomposition above the existing Task workflow. It does
not start agents or mutate task states; Tasks remain SceneWorks' executable unit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_context
from app.context import AppContext
from app.models import Initiative, Project, Task, WorkPackage
from app.schemas import (
    InitiativeCreate,
    InitiativeOut,
    InitiativeUpdate,
    WorkPackageCreate,
    WorkPackageOut,
    WorkPackageUpdate,
)

router = APIRouter(tags=["initiatives"])


async def _initiative_out(ctx: AppContext, initiative: Initiative) -> InitiativeOut:
    async with ctx.engine_factory() as session:
        wp_count = (
            await session.execute(
                select(func.count(WorkPackage.id)).where(
                    WorkPackage.initiative_id == initiative.id
                )
            )
        ).scalar() or 0
        completed = (
            await session.execute(
                select(func.count(WorkPackage.id)).where(
                    WorkPackage.initiative_id == initiative.id,
                    WorkPackage.status == "completed",
                )
            )
        ).scalar() or 0
        task_count = (
            await session.execute(
                select(func.count(Task.id))
                .join(WorkPackage, Task.work_package_id == WorkPackage.id)
                .where(WorkPackage.initiative_id == initiative.id)
            )
        ).scalar() or 0
    out = InitiativeOut.model_validate(initiative)
    out.work_package_count = int(wp_count)
    out.completed_work_packages = int(completed)
    out.task_count = int(task_count)
    return out


async def _work_package_out(ctx: AppContext, work_package: WorkPackage) -> WorkPackageOut:
    async with ctx.engine_factory() as session:
        task_count = (
            await session.execute(
                select(func.count(Task.id)).where(Task.work_package_id == work_package.id)
            )
        ).scalar() or 0
    out = WorkPackageOut.model_validate(work_package)
    out.task_count = int(task_count)
    return out


async def _validated_dependencies(
    session,
    initiative_id: int,
    dependency_ids: list[int],
    *,
    current_id: int | None = None,
) -> list[int]:
    dependencies = list(dict.fromkeys(dependency_ids))
    if current_id is not None and current_id in dependencies:
        raise HTTPException(400, "a work package cannot depend on itself")
    if dependencies:
        rows = list(
            (
                await session.execute(
                    select(WorkPackage).where(WorkPackage.id.in_(dependencies))
                )
            ).scalars()
        )
        if len(rows) != len(dependencies) or any(
            row.initiative_id != initiative_id for row in rows
        ):
            raise HTTPException(
                400, "all dependencies must be existing work packages in the same initiative"
            )

    # Creation can only point backward to existing packages and therefore cannot
    # introduce a cycle. Updates can, so validate the full graph when current_id
    # is known.
    if current_id is not None:
        packages = list(
            (
                await session.execute(
                    select(WorkPackage).where(WorkPackage.initiative_id == initiative_id)
                )
            ).scalars()
        )
        graph = {row.id: set(row.depends_on or []) for row in packages}
        graph[current_id] = set(dependencies)
        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(node: int) -> None:
            if node in visiting:
                raise HTTPException(400, "work-package dependencies must be acyclic")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph.get(node, set()):
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)

    return dependencies


@router.get("/api/projects/{project_id}/initiatives")
async def list_initiatives(
    project_id: int,
    ctx: AppContext = Depends(get_context),
) -> list[InitiativeOut]:
    async with ctx.engine_factory() as session:
        if await session.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")
        initiatives = list(
            (
                await session.execute(
                    select(Initiative)
                    .where(Initiative.project_id == project_id)
                    .order_by(Initiative.updated_at.desc(), Initiative.id.desc())
                )
            ).scalars()
        )
    return [await _initiative_out(ctx, initiative) for initiative in initiatives]


@router.post("/api/projects/{project_id}/initiatives", status_code=201)
async def create_initiative(
    project_id: int,
    body: InitiativeCreate,
    ctx: AppContext = Depends(get_context),
) -> InitiativeOut:
    async with ctx.engine_factory() as session:
        if await session.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")
        initiative = Initiative(
            project_id=project_id,
            title=body.title,
            objective=body.objective,
            description=body.description,
            status="planned",
        )
        session.add(initiative)
        await session.commit()
        await session.refresh(initiative)
    return await _initiative_out(ctx, initiative)


@router.get("/api/initiatives/{initiative_id}")
async def get_initiative(
    initiative_id: int,
    ctx: AppContext = Depends(get_context),
) -> InitiativeOut:
    async with ctx.engine_factory() as session:
        initiative = await session.get(Initiative, initiative_id)
        if initiative is None:
            raise HTTPException(404, "initiative not found")
    return await _initiative_out(ctx, initiative)


@router.patch("/api/initiatives/{initiative_id}")
async def update_initiative(
    initiative_id: int,
    body: InitiativeUpdate,
    ctx: AppContext = Depends(get_context),
) -> InitiativeOut:
    async with ctx.engine_factory() as session:
        initiative = await session.get(Initiative, initiative_id)
        if initiative is None:
            raise HTTPException(404, "initiative not found")
        data = body.model_dump(exclude_unset=True)
        if data.get("status") == "completed":
            unfinished = (
                await session.execute(
                    select(func.count(WorkPackage.id)).where(
                        WorkPackage.initiative_id == initiative_id,
                        WorkPackage.status.not_in(("completed", "cancelled")),
                    )
                )
            ).scalar() or 0
            if unfinished:
                raise HTTPException(
                    409, "initiative cannot complete while work packages remain unfinished"
                )
        for key, value in data.items():
            setattr(initiative, key, value)
        await session.commit()
        await session.refresh(initiative)
    return await _initiative_out(ctx, initiative)


@router.get("/api/initiatives/{initiative_id}/work-packages")
async def list_work_packages(
    initiative_id: int,
    ctx: AppContext = Depends(get_context),
) -> list[WorkPackageOut]:
    async with ctx.engine_factory() as session:
        if await session.get(Initiative, initiative_id) is None:
            raise HTTPException(404, "initiative not found")
        rows = list(
            (
                await session.execute(
                    select(WorkPackage)
                    .where(WorkPackage.initiative_id == initiative_id)
                    .order_by(WorkPackage.sequence, WorkPackage.id)
                )
            ).scalars()
        )
    return [await _work_package_out(ctx, row) for row in rows]


@router.post("/api/initiatives/{initiative_id}/work-packages", status_code=201)
async def create_work_package(
    initiative_id: int,
    body: WorkPackageCreate,
    ctx: AppContext = Depends(get_context),
) -> WorkPackageOut:
    async with ctx.engine_factory() as session:
        if await session.get(Initiative, initiative_id) is None:
            raise HTTPException(404, "initiative not found")
        dependencies = await _validated_dependencies(
            session, initiative_id, body.depends_on
        )
        sequence = body.sequence
        if sequence is None:
            highest = (
                await session.execute(
                    select(func.max(WorkPackage.sequence)).where(
                        WorkPackage.initiative_id == initiative_id
                    )
                )
            ).scalar()
            sequence = int(highest or 0) + 1
        row = WorkPackage(
            initiative_id=initiative_id,
            key=body.key,
            title=body.title,
            description=body.description,
            sequence=sequence,
            depends_on=dependencies,
            acceptance_criteria=body.acceptance_criteria,
            status="planned",
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                409, f"work-package key {body.key!r} already exists in this initiative"
            ) from exc
        await session.refresh(row)
    return await _work_package_out(ctx, row)


@router.get("/api/work-packages/{work_package_id}")
async def get_work_package(
    work_package_id: int,
    ctx: AppContext = Depends(get_context),
) -> WorkPackageOut:
    async with ctx.engine_factory() as session:
        row = await session.get(WorkPackage, work_package_id)
        if row is None:
            raise HTTPException(404, "work package not found")
    return await _work_package_out(ctx, row)


@router.patch("/api/work-packages/{work_package_id}")
async def update_work_package(
    work_package_id: int,
    body: WorkPackageUpdate,
    ctx: AppContext = Depends(get_context),
) -> WorkPackageOut:
    async with ctx.engine_factory() as session:
        row = await session.get(WorkPackage, work_package_id)
        if row is None:
            raise HTTPException(404, "work package not found")
        data = body.model_dump(exclude_unset=True)
        if "depends_on" in data:
            data["depends_on"] = await _validated_dependencies(
                session,
                row.initiative_id,
                data["depends_on"] or [],
                current_id=row.id,
            )
        for key, value in data.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
    return await _work_package_out(ctx, row)
