"""Backend health + roles + settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.agents.model_routing import ModelRouter, ModelRoutingError
from app.agents.registry import BackendRegistry
from app.api.deps import get_context
from app.context import AppContext
from app.git.workspace import GitWorktreeService
from app.schemas import BackendOut, RoleOut, SettingsOut, SettingsUpdate
from app.services.engineering_sessions import EngineeringSessionService
from app.services.settings import (
    ADVANCED_PERMISSIONS,
    MCP_MODES,
    ROLE_KEYS,
    ROLE_MODEL_PROFILES,
    apply_overrides,
)

backends_router = APIRouter(prefix="/api/backends", tags=["backends"])
roles_router = APIRouter(prefix="/api/roles", tags=["roles"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


@backends_router.get("")
async def list_backends(
    refresh: bool = False, ctx: AppContext = Depends(get_context)
) -> list[BackendOut]:
    healths = await ctx.backends.health_all(force=refresh)
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


def _routing_state(ctx: AppContext) -> dict:
    roles = []
    for effective in ctx.roles.all():
        base = ctx.roles.get(effective.key)
        try:
            resolved = ctx.model_router.resolve(effective)
            resolved_backend = resolved.backend
            resolved_model = resolved.model
            routing_source = resolved.source
            routing_error = None
        except ModelRoutingError as exc:
            resolved_backend = effective.backend
            resolved_model = None
            routing_source = "error"
            routing_error = str(exc)
        roles.append(
            {
                "key": effective.key,
                "display_name": effective.display_name,
                "default_profile": base.model_profile,
                "effective_profile": effective.model_profile,
                "profile_source": ctx.roles.profile_source(effective.key),
                "resolved_backend": resolved_backend,
                "resolved_model": resolved_model,
                "routing_source": routing_source,
                "routing_error": routing_error,
            }
        )
    return {
        "profiles": sorted(ROLE_MODEL_PROFILES),
        "role_profile_overrides": dict(ctx.settings.role_model_profile_overrides),
        "roles": roles,
    }


@settings_router.get("")
async def get_settings(ctx: AppContext = Depends(get_context)) -> SettingsOut:
    settings = ctx.settings
    healths = await ctx.backends.health_all()
    return SettingsOut(
        worktree_root=str(settings.worktree_root),
        gemini_executable=settings.gemini_executable,
        gemini_model=settings.gemini_model,
        gemini_extra_args=list(settings.gemini_extra_args),
        opencode_executable=settings.opencode_executable,
        opencode_model=settings.opencode_model,
        opencode_agent=settings.opencode_agent,
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


@settings_router.get("/routing")
async def get_routing_settings(ctx: AppContext = Depends(get_context)) -> dict:
    """Return role -> profile -> concrete backend/model resolution."""
    return _routing_state(ctx)


@settings_router.patch("/routing")
async def update_routing_settings(
    body: dict, ctx: AppContext = Depends(get_context)
) -> dict:
    unknown_keys = set(body) - {"role_profile_overrides"}
    if unknown_keys:
        raise HTTPException(
            status_code=422,
            detail="unknown routing settings: " + ", ".join(sorted(unknown_keys)),
        )
    raw = body.get("role_profile_overrides", {})
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="role_profile_overrides must be an object")
    normalized: dict[str, str] = {}
    for raw_role, raw_profile in raw.items():
        role = str(raw_role).strip()
        profile = str(raw_profile).strip().lower()
        if role not in ROLE_KEYS:
            raise HTTPException(status_code=422, detail=f"unknown role: {role}")
        if not profile:
            continue
        if profile not in ROLE_MODEL_PROFILES:
            raise HTTPException(
                status_code=422,
                detail=f"invalid profile {profile!r}; expected one of: " + ", ".join(sorted(ROLE_MODEL_PROFILES)),
            )
        normalized[role] = profile
    try:
        await _apply_patch(ctx, {"role_model_profile_overrides": normalized})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _routing_state(ctx)


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
        "available_runtimes": ctx.runtimes.keys(),
        "available_backends": [key for key in ctx.backends.keys() if key != "fake"],
        "default_backend": settings.default_backend,
        "action_tools_enabled": mode in {"standard", "advanced"},
        "advanced_agent_sessions_enabled": mode == "advanced",
        "direct_engineering_sessions_enabled": mode == "advanced",
        "semantic_pcs_control_enabled": mode == "advanced",
        "pcs_gui_observation_enabled": mode == "advanced",
        "pcs_gui_automation_enabled": mode == "advanced",
        "advanced_warning": (
            "Advanced mode gives the MCP client SceneWorks-owned repository, command, "
            "process, Git, semantic PCS and managed-PCS GUI capabilities. GUI automation "
            "requires the separate gui_automate permission and is constrained to UI "
            "Automation controls inside the live SceneWorks-managed PCS window; no raw "
            "desktop coordinates, arbitrary HWNDs or arbitrary PIDs are exposed. Worktree "
            "paths are confined; external PCS assets require an explicit project alias plus "
            "external_asset_read. Command/process execution is not an OS sandbox and runs "
            "with the SceneWorks user's OS authority; shell-capable processes may also "
            "access the network unless the host is sandboxed separately."
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
    """Apply persisted operational settings to every live consumer.

    WP13 rebuilt ``ctx.backends`` but left ExecutionEngine and workflow services
    pointing at the old registry/router. WP14 rewires the dependency graph;
    WP16/WP17/WP18 keep PCS and GUI services attached to the rebuilt
    EngineeringSession service and current Settings object as well.
    """
    overrides = await ctx.settings_store.update(patch)
    ctx.settings_overrides = overrides
    ctx.settings = apply_overrides(ctx.settings, overrides)

    new_git = GitWorktreeService(ctx.settings)
    new_backends = BackendRegistry(ctx.settings)
    new_router = ModelRouter(ctx.settings, new_backends.keys())

    await ctx.backends.shutdown()
    ctx.git = new_git
    ctx.backends = new_backends
    ctx.model_router = new_router

    ctx.execution_engine._backends = new_backends
    ctx.execution_engine._settings = ctx.settings
    ctx.workflow._git = new_git
    ctx.workflow._settings = ctx.settings
    ctx.workflow._model_router = new_router
    ctx.workflow_manager._git = new_git
    ctx.workflow_manager._settings = ctx.settings
    ctx.workflow_manager._runtime._git = new_git
    ctx.workflow_manager._runtime._model_router = new_router
    ctx.workflow_manager._roles_runtime._git = new_git
    ctx.workflow_manager._advisory_runtime._git = new_git
    ctx.agent_sessions._git = new_git
    ctx.agent_sessions._settings = ctx.settings
    ctx.engineering_sessions = EngineeringSessionService(
        ctx.engine_factory, new_git, ctx.runtimes, ctx.settings
    )
    ctx.pcs_control._engineering_sessions = ctx.engineering_sessions
    ctx.gui_evidence._engineering_sessions = ctx.engineering_sessions
    ctx.gui_evidence._settings = ctx.settings
    ctx.gui_automation._engineering_sessions = ctx.engineering_sessions
    ctx.gui_automation._pcs = ctx.pcs_control
    ctx.gui_automation._gui = ctx.gui_evidence

    ctx.roles.set_default_backend(
        ctx.settings.default_backend
        if ctx.settings.default_backend != "gemini_acp"
        else None
    )
    ctx.roles.set_model_profile_overrides(ctx.settings.role_model_profile_overrides)
