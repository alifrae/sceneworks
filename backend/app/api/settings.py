"""Backend health + roles + settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agents.registry import BackendRegistry
from app.api.deps import get_context
from app.context import AppContext
from app.git.workspace import GitWorktreeService
from app.schemas import BackendOut, RoleOut, SettingsOut, SettingsUpdate
from app.services.settings import apply_overrides

backends_router = APIRouter(prefix="/api/backends", tags=["backends"])
roles_router = APIRouter(prefix="/api/roles", tags=["roles"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


@backends_router.get("")
async def list_backends(ctx: AppContext = Depends(get_context)) -> list[BackendOut]:
    healths = await ctx.backends.health_all()
    return [BackendOut.model_validate(h.__dict__) for h in healths]


@roles_router.get("")
async def list_roles(ctx: AppContext = Depends(get_context)) -> list[RoleOut]:
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
            persona=r.persona,
            core_capabilities=list(r.core_capabilities),
        )
        for r in ctx.roles.all()
    ]


@settings_router.get("")
async def get_settings(ctx: AppContext = Depends(get_context)) -> SettingsOut:
    settings = ctx.settings
    healths = await ctx.backends.health_all()
    return SettingsOut(
        worktree_root=str(settings.worktree_root),
        gemini_executable=settings.gemini_executable,
        gemini_model=settings.gemini_model,
        gemini_extra_args=list(settings.gemini_extra_args),
        model_profile_routes={
            profile: route.model_dump()
            for profile, route in settings.model_profile_routes.items()
        },
        execution_timeout_seconds=settings.execution_timeout_seconds,
        cancel_grace_seconds=settings.cancel_grace_seconds,
        default_backend=settings.default_backend,
        log_level=settings.log_level,
        context_max_bytes=settings.context_max_bytes,
        database_url=settings.database_url,
        backends=[BackendOut.model_validate(h.__dict__) for h in healths],
    )


@settings_router.patch("")
async def update_settings(
    body: SettingsUpdate, ctx: AppContext = Depends(get_context)
) -> SettingsOut:
    patch = body.model_dump(exclude_unset=True, exclude_none=True)
    if patch:
        overrides = await ctx.settings_store.update(patch)
        ctx.settings_overrides = overrides
        # apply_overrides mutates the shared Settings instance. Existing model
        # routers and provider registries therefore see route changes without
        # rewriting already-persisted Executions.
        ctx.settings = apply_overrides(ctx.settings, overrides)
        ctx.git = GitWorktreeService(ctx.settings)
        ctx.backends = BackendRegistry(ctx.settings)
        ctx.roles.set_default_backend(
            ctx.settings.default_backend
            if ctx.settings.default_backend != "gemini_acp"
            else None
        )
    return await get_settings(ctx)
