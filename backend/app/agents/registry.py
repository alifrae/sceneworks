"""Backend registry and execution-scoped model binding."""

from __future__ import annotations

import asyncio
import time

from app.agents.base import AgentBackend, AgentEventSink, AgentRequest, AgentResult, BackendHealth, Workspace
from app.agents.fake import FakeAgentBackend
from app.agents.gemini_acp import GeminiACPBackend
from app.agents.gemini_acp_attachments import AttachmentAwareGeminiACPBackend
from app.agents.openhands import OpenHandsBackend
from app.config.settings import Settings

HEALTH_CACHE_SECONDS = 60.0
HEALTH_PROBE_TIMEOUT_SECONDS = 15.0


class BackendNotFoundError(KeyError):
    pass


class _FixedModelOpenHandsBackend(OpenHandsBackend):
    """OpenHands adapter whose model cannot drift with process environment."""

    def __init__(self, settings: Settings, model: str):
        super().__init__(settings)
        self._fixed_model = model

    def _model(self) -> str | None:
        return self._fixed_model


class _ExecutionModelProxy:
    """Bind AgentRequest.model to a provider instance for one execution.

    The normal backends remain unaware of SceneWorks profile routing. This proxy
    constructs an execution-scoped provider instance when a concrete model was
    persisted on the Execution row, and keeps that exact instance available to
    cancellation while the run is active.
    """

    def __init__(self, base: AgentBackend, settings: Settings):
        self._base = base
        self._settings = settings
        self._active: dict[str, AgentBackend] = {}
        self.key = base.key
        self.label = base.label

    def _target(self, model: str | None) -> AgentBackend:
        if not model:
            return self._base
        if isinstance(self._base, GeminiACPBackend):
            env = dict(self._settings.gemini_environment)
            env["GEMINI_MODEL"] = model
            routed = self._settings.model_copy(
                deep=True,
                update={"gemini_model": model, "gemini_environment": env},
            )
            return AttachmentAwareGeminiACPBackend(routed)
        if isinstance(self._base, OpenHandsBackend):
            routed = self._settings.model_copy(
                deep=True,
                update={"openhands_model": model},
            )
            return _FixedModelOpenHandsBackend(routed, model)
        return self._base

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult:
        target = self._target(request.model)
        self._active[request.execution_id] = target
        try:
            return await target.run(request, workspace, event_sink)
        finally:
            self._active.pop(request.execution_id, None)

    async def cancel(self, execution_id: str) -> None:
        target = self._active.get(execution_id, self._base)
        await target.cancel(execution_id)

    async def health(self) -> BackendHealth:
        return await self._base.health()


class BackendRegistry:
    def __init__(self, settings: Settings, include_fake: bool = True, include_openhands: bool = True):
        self._settings = settings
        self._backends: dict[str, AgentBackend] = {
            "gemini_acp": AttachmentAwareGeminiACPBackend(settings),
        }
        if include_openhands:
            self._backends["openhands"] = OpenHandsBackend(settings)
        if include_fake:
            self._backends["fake"] = FakeAgentBackend()
        self._proxies: dict[str, AgentBackend] = {}
        self._health_cache: list[BackendHealth] | None = None
        self._health_checked_at = 0.0
        self._health_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    def register(self, key: str, backend: AgentBackend) -> None:
        """Replace a registered backend, primarily for tests/qualification."""
        self._backends[key] = backend
        self._proxies.pop(key, None)
        self._health_cache = None
        self._health_checked_at = 0.0

    def get(self, key: str) -> AgentBackend:
        try:
            backend = self._backends[key]
        except KeyError:
            raise BackendNotFoundError(
                f"backend {key!r} is not registered "
                f"(available: {', '.join(sorted(self._backends))})"
            ) from None

        if not isinstance(backend, (GeminiACPBackend, OpenHandsBackend)):
            return backend
        proxy = self._proxies.get(key)
        if proxy is None:
            proxy = _ExecutionModelProxy(backend, self._settings)
            self._proxies[key] = proxy
        return proxy

    def keys(self) -> list[str]:
        return list(self._backends.keys())

    async def shutdown(self) -> None:
        """Cancel any stale-while-revalidate health work owned by this registry."""
        task = self._refresh_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _cold_health(self) -> list[BackendHealth]:
        """Return truthful cheap state while provider probes run in background.

        The scripted backend has no external dependency, so reporting it as
        unavailable during a slow Gemini/OpenHands probe is simply wrong. Real
        providers stay explicitly in a probing state until their checks finish.
        """
        healths: list[BackendHealth] = []
        for key, backend in self._backends.items():
            if isinstance(backend, FakeAgentBackend):
                healths.append(
                    BackendHealth(
                        key=key,
                        label=getattr(backend, "label", key),
                        available=True,
                        version="fake-1.0",
                        detail="scripted test backend; no external provider required",
                    )
                )
            else:
                healths.append(
                    BackendHealth(
                        key=key,
                        label=getattr(backend, "label", key),
                        available=False,
                        detail="probing...",
                    )
                )
        return healths

    async def health_all(self, force: bool = False) -> list[BackendHealth]:
        """Health of every backend, served stale-while-revalidate."""
        if force:
            return await self._probe()

        cached = self._health_cache
        if cached is not None:
            if (time.monotonic() - self._health_checked_at) >= HEALTH_CACHE_SECONDS:
                self._schedule_refresh()
            return cached

        self._schedule_refresh()
        return self._cold_health()

    def _schedule_refresh(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh_quietly())

    async def _refresh_quietly(self) -> None:
        try:
            await self._probe()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async def _probe_one(self, key: str, backend: AgentBackend) -> BackendHealth:
        try:
            return await asyncio.wait_for(
                backend.health(), timeout=HEALTH_PROBE_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return BackendHealth(
                key=key,
                label=getattr(backend, "label", key),
                available=False,
                detail=f"health check timed out after {HEALTH_PROBE_TIMEOUT_SECONDS:g}s",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - health must isolate providers
            return BackendHealth(
                key=key,
                label=getattr(backend, "label", key),
                available=False,
                detail=f"health check failed: {exc}",
            )

    async def _probe(self) -> list[BackendHealth]:
        async with self._health_lock:
            # Providers are independent. Sequential probing allowed one slow or
            # broken provider to keep every backend (including Fake) red.
            healths = await asyncio.gather(
                *(
                    self._probe_one(key, backend)
                    for key, backend in self._backends.items()
                )
            )
            self._health_cache = list(healths)
            self._health_checked_at = time.monotonic()
            return self._health_cache
