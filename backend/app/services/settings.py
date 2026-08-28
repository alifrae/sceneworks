"""Persisted settings overrides.

Effective configuration = environment/.env defaults overridden by database
values (editable from the Settings page). Secrets never live here: only
non-sensitive operational knobs are persistable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import ModelProfileRoute, Settings
from app.models import AppSetting

MCP_MODES = {"observe", "standard", "advanced"}
ADVANCED_PERMISSIONS = {
    "repository_read",
    "repository_write",
    "shell_execute",
    "process_control",
    "git_commit",
    "network_access",
    "agent_delegate",
    "subagents",  # legacy Gemini provider-session capability
}

EDITABLE_KEYS = {
    "worktree_root": str,
    "gemini_executable": str,
    "gemini_model": str,
    "opencode_executable": str,
    "opencode_model": str,
    "opencode_agent": str,
    "model_profile_routes": dict,
    "execution_timeout_seconds": int,
    "default_backend": str,
    "mcp_enabled": bool,
    "mcp_mode": str,
    "mcp_tool_max_chars": int,
    "advanced_session_permissions": list,
}


@dataclass
class SettingsOverrides:
    values: dict[str, object] = field(default_factory=dict)


class SettingsStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def load(self) -> SettingsOverrides:
        async with self._session_factory() as session:
            rows = (await session.execute(select(AppSetting))).scalars().all()
        return SettingsOverrides(values={r.key: r.value for r in rows})

    async def update(self, patch: dict) -> SettingsOverrides:
        normalized = {
            key: patch[key] for key in EDITABLE_KEYS if key in patch and patch[key] is not None
        }
        async with self._session_factory() as session:
            for key, value in normalized.items():
                row = await session.get(AppSetting, key)
                if value == "" and isinstance(value, str):
                    if row:
                        await session.delete(row)
                    continue
                if row:
                    row.value = value
                else:
                    session.add(AppSetting(key=key, value=value))
            await session.commit()
        return await self.load()

    async def clear(self) -> None:
        async with self._session_factory() as session:
            rows = (await session.execute(select(AppSetting))).scalars().all()
            for row in rows:
                await session.delete(row)
            await session.commit()


def apply_overrides(settings: Settings, overrides: SettingsOverrides) -> Settings:
    values = overrides.values
    if "worktree_root" in values:
        settings.worktree_root = Path(str(values["worktree_root"]))
    if "gemini_executable" in values:
        settings.gemini_executable = str(values["gemini_executable"]) or None
    if "gemini_model" in values:
        settings.gemini_model = str(values["gemini_model"]) or None
    if "opencode_executable" in values:
        settings.opencode_executable = str(values["opencode_executable"]) or None
    if "opencode_model" in values:
        settings.opencode_model = str(values["opencode_model"]) or None
    if "opencode_agent" in values:
        settings.opencode_agent = str(values["opencode_agent"]) or None
    if "model_profile_routes" in values:
        raw = values["model_profile_routes"]
        if not isinstance(raw, dict):
            raise ValueError("model_profile_routes override must be an object")
        settings.model_profile_routes = {
            str(profile): ModelProfileRoute.model_validate(route)
            for profile, route in raw.items()
        }
    if "execution_timeout_seconds" in values:
        settings.execution_timeout_seconds = int(values["execution_timeout_seconds"])
    if "default_backend" in values:
        backend = str(values["default_backend"])
        if backend not in {"gemini_acp", "opencode", "openhands", "fake"}:
            raise ValueError(f"invalid default_backend: {backend!r}")
        settings.default_backend = backend  # type: ignore[assignment]
    if "mcp_enabled" in values:
        settings.mcp_enabled = bool(values["mcp_enabled"])
    if "mcp_mode" in values:
        mode = str(values["mcp_mode"])
        if mode not in MCP_MODES:
            raise ValueError(f"invalid mcp_mode: {mode!r}")
        settings.mcp_mode = mode  # type: ignore[assignment]
    if "mcp_tool_max_chars" in values:
        settings.mcp_tool_max_chars = int(values["mcp_tool_max_chars"])
    if "advanced_session_permissions" in values:
        raw_permissions = values["advanced_session_permissions"]
        if not isinstance(raw_permissions, list):
            raise ValueError("advanced_session_permissions override must be an array")
        permissions = [str(item) for item in raw_permissions]
        unknown = set(permissions) - ADVANCED_PERMISSIONS
        if unknown:
            raise ValueError(
                "unknown advanced_session_permissions: " + ", ".join(sorted(unknown))
            )
        settings.advanced_session_permissions = permissions
    return settings
