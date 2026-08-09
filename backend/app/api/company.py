"""Company page routes: roles, manual asks, stored decisions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_context
from app.context import AppContext
from app.models import Artifact
from app.schemas import ArtifactOut, CompanyAskRequest, ExecutionOut, RoleOut
from app.services.workflow import WorkflowError

router = APIRouter(prefix="/api/company", tags=["company"])


@router.get("/roles")
async def list_company_roles(ctx: AppContext = Depends(get_context)) -> list[RoleOut]:
    return [
        RoleOut(
            key=r.key,
            display_name=r.display_name,
            description=r.description,
            backend=r.backend,
            model_profile=r.model_profile,
            permissions=sorted(p.value for p in r.permissions),
            can_modify_source=r.can_modify_source,
            can_commit=r.can_commit,
            responsibilities=list(r.responsibilities),
        )
        for r in ctx.roles.all()
    ]


@router.post("/ask", status_code=201)
async def ask_company_role(
    body: CompanyAskRequest, ctx: AppContext = Depends(get_context)
) -> ExecutionOut:
    try:
        execution = await ctx.company.ask(body.role, body.project_id, body.question)
    except WorkflowError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return ExecutionOut.model_validate(execution)


@router.get("/artifacts")
async def list_artifacts(
    role: str | None = None, ctx: AppContext = Depends(get_context)
) -> list[ArtifactOut]:
    async with ctx.engine_factory() as session:
        stmt = select(Artifact).order_by(Artifact.created_at.desc()).limit(200)
        if role:
            stmt = stmt.where(Artifact.role == role)
        rows = (await session.execute(stmt)).scalars().all()
    return [ArtifactOut.model_validate(r) for r in rows]
