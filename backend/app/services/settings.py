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

EDITABLE_KEYS = {
    "worktree_root": str,
    "gemini_executable": str,
    "gemini_model": str,
    "model_profile_routes": dict,
    "execution_timeout_seconds": int,
    "default_backend": str,
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
        settings.default_backend = str(values["default_backend"])
    return settings
