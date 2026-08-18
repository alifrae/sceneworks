"""Project Policy API routes (WP4).

A project's engineering contract: protected paths, required review checks,
architecture invariants, and the other structured categories in
`app.models.ProjectPolicy`. One policy per project.

``GET`` never 404s for an unconfigured project -- it returns an empty policy
(all lists ``[]``, ``id``/timestamps ``null``), matching how
``Project.test_commands`` is always present and defaults to ``[]`` rather than
requiring a caller to first check whether it exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_context
from app.context import AppContext
from app.models import Project
from app.schemas import ProjectPolicyIn, ProjectPolicyOut

router = APIRouter(prefix="/api/projects/{project_id}/policy", tags=["policy"])


@router.get("", response_model=ProjectPolicyOut)
async def get_policy(
    project_id: int,
    ctx: AppContext = Depends(get_context),
):
    async with ctx.engine_factory() as session:
        if await session.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")

    policy = await ctx.policy.get_or_default(project_id)
    return ProjectPolicyOut.model_validate(policy)


@router.put("", response_model=ProjectPolicyOut)
async def put_policy(
    project_id: int,
    body: ProjectPolicyIn,
    ctx: AppContext = Depends(get_context),
):
    async with ctx.engine_factory() as session:
        if await session.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")

    policy = await ctx.policy.upsert(
        project_id,
        protected_paths=body.protected_paths,
        go_no_go_commands=body.go_no_go_commands,
        forbidden_dependency_directions=body.forbidden_dependency_directions,
        architecture_invariants=body.architecture_invariants,
        documentation_requirements=body.documentation_requirements,
        performance_constraints=body.performance_constraints,
        required_review_checks=body.required_review_checks,
        release_requirements=body.release_requirements,
        policy_file_paths=body.policy_file_paths,
    )
    return ProjectPolicyOut.model_validate(policy)


@router.delete("", status_code=204)
async def delete_policy(
    project_id: int,
    ctx: AppContext = Depends(get_context),
):
    """Clear a project's policy entirely (distinct from PUT with empty lists,
    which still leaves a policy row -- this removes it)."""
    async with ctx.engine_factory() as session:
        if await session.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")

    await ctx.policy.delete(project_id)
