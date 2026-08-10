"""Backend registry: the only place backends are constructed and looked up."""

from __future__ import annotations

import asyncio
import time

from app.agents.base import AgentBackend, BackendHealth
from app.agents.fake import FakeAgentBackend
from app.agents.gemini_acp import GeminiACPBackend
from app.agents.openhands import OpenHandsBackend
from app.config.settings import Settings

# A health probe shells out to the agent CLI (`gemini --version`), which costs
# seconds. The dashboard and settings pages poll health on every load, so
# results are cached; without this the UI blocks on subprocess startup.
HEALTH_CACHE_SECONDS = 60.0


class BackendNotFoundError(KeyError):
    pass


class BackendRegistry:
    def __init__(self, settings: Settings, include_fake: bool = True, include_openhands: bool = True):
        self._backends: dict[str, AgentBackend] = {
            "gemini_acp": GeminiACPBackend(settings),
        }
        if include_openhands:
            self._backends["openhands"] = OpenHandsBackend(settings)
        if include_fake:
            self._backends["fake"] = FakeAgentBackend()
        self._health_cache: list[BackendHealth] | None = None
        self._health_checked_at = 0.0
        self._health_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    def get(self, key: str) -> AgentBackend:
        try:
            return self._backends[key]
        except KeyError:
            raise BackendNotFoundError(
                f"backend {key!r} is not registered "
                f"(available: {', '.join(sorted(self._backends))})"
            ) from None

    def keys(self) -> list[str]:
        return list(self._backends.keys())

    async def health_all(self, force: bool = False) -> list[BackendHealth]:
        """Health of every backend, served stale-while-revalidate.

        A probe shells out to the agent CLI and can take tens of seconds, so a
        request must never wait on one. Cached results are returned
        immediately and refreshed in the background when stale; only the very
        first call (cold cache) awaits a probe. Pass force=True to await a
        fresh probe regardless.
        """
        if force:
            return await self._probe()

        cached = self._health_cache
        if cached is None:
            return await self._probe()

        if (time.monotonic() - self._health_checked_at) >= HEALTH_CACHE_SECONDS:
            self._schedule_refresh()
        return cached

    def _schedule_refresh(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh_quietly())

    async def _refresh_quietly(self) -> None:
        try:
            await self._probe()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed probe must not surface here
            pass

    async def _probe(self) -> list[BackendHealth]:
        async with self._health_lock:
            healths = [await backend.health() for backend in self._backends.values()]
            self._health_cache = healths
            self._health_checked_at = time.monotonic()
            return healths
