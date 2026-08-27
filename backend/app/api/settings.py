"""Backend health + roles + settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.agents.registry import BackendRegistry
from app.api.deps import get_context
from app.context import AppContext
from app.git.workspace import GitWorktreeService
from app.schemas import BackendOut, RoleOut, SettingsOut, SettingsUpdate
from app.services.settings import ADVANCED_PERMISSIONS, MCP_MODES, apply_overrides

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
        await _apply_patch(ctx, patch)
    return await get_settings(ctx)


@settings_router.get("/mcp")
async def get_mcp_settings(ctx: AppContext = Depends(get_context)) -> dict:
    """Operational MCP/ChatGPT settings without exposing secrets."""
    settings = ctx.settings
    mode = settings.effective_mcp_mode
    return {
        "enabled": settings.mcp_enabled,
        "mode": settings.mcp_mode,
        "effective_mode": mode,
        "endpoint": "/mcp",
        "tool_max_chars": settings.mcp_tool_max_chars,
        "advanced_session_permissions": list(settings.advanced_session_permissions),
        "available_advanced_permissions": sorted(ADVANCED_PERMISSIONS),
        "action_tools_enabled": mode in {"standard", "advanced"},
        "advanced_agent_sessions_enabled": mode == "advanced",
        "advanced_warning": (
            "Advanced mode lets ChatGPT supervise Gemini CLI in an isolated Git "
            "worktree. File access is worktree-confined, but shell execution is "
            "not an OS sandbox and runs with the SceneWorks user's OS authority."
        ),
    }


@settings_router.patch("/mcp")
async def update_mcp_settings(
    body: dict, ctx: AppContext = Depends(get_context)
) -> dict:
    """Persist the MCP operating mode and Advanced-session capability allowlist."""
    allowed_keys = {
        "mcp_enabled",
        "mcp_mode",
        "mcp_tool_max_chars",
        "advanced_session_permissions",
    }
    unknown_keys = set(body) - allowed_keys
    if unknown_keys:
        raise HTTPException(
            status_code=422,
            detail="unknown MCP settings: " + ", ".join(sorted(unknown_keys)),
        )

    patch: dict = {}
    if "mcp_enabled" in body:
        if not isinstance(body["mcp_enabled"], bool):
            raise HTTPException(status_code=422, detail="mcp_enabled must be boolean")
        patch["mcp_enabled"] = body["mcp_enabled"]
    if "mcp_mode" in body:
        mode = str(body["mcp_mode"])
        if mode not in MCP_MODES:
            raise HTTPException(
                status_code=422,
                detail="mcp_mode must be one of: observe, standard, advanced",
            )
        patch["mcp_mode"] = mode
        # New explicit mode selection supersedes the prototype compatibility flag.
        ctx.settings.mcp_allow_actions = False
    if "mcp_tool_max_chars" in body:
        try:
            limit = int(body["mcp_tool_max_chars"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="mcp_tool_max_chars must be an integer") from exc
        if not 10_000 <= limit <= 1_000_000:
            raise HTTPException(
                status_code=422,
                detail="mcp_tool_max_chars must be between 10000 and 1000000",
            )
        patch["mcp_tool_max_chars"] = limit
    if "advanced_session_permissions" in body:
        raw = body["advanced_session_permissions"]
        if not isinstance(raw, list):
            raise HTTPException(
                status_code=422,
                detail="advanced_session_permissions must be an array",
            )
        permissions = [str(item) for item in raw]
        unknown = set(permissions) - ADVANCED_PERMISSIONS
        if unknown:
            raise HTTPException(
                status_code=422,
                detail="unknown advanced permissions: " + ", ".join(sorted(unknown)),
            )
        patch["advanced_session_permissions"] = permissions

    if patch:
        try:
            await _apply_patch(ctx, patch)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await get_mcp_settings(ctx)


async def _apply_patch(ctx: AppContext, patch: dict) -> None:
    overrides = await ctx.settings_store.update(patch)
    ctx.settings_overrides = overrides
    # apply_overrides mutates the shared Settings instance. Existing model
    # routers, the Advanced session service and provider registries therefore
    # see route/policy changes without rewriting persisted executions/sessions.
    ctx.settings = apply_overrides(ctx.settings, overrides)
    ctx.git = GitWorktreeService(ctx.settings)
    ctx.backends = BackendRegistry(ctx.settings)
    ctx.roles.set_default_backend(
        ctx.settings.default_backend
        if ctx.settings.default_backend != "gemini_acp"
        else None
    )
