"""Control-plane integrity extensions for provider health diagnostics."""

from __future__ import annotations

import asyncio

from app.agents.base import AgentBackend, BackendHealth
from app.agents.registry import BackendRegistry, HEALTH_PROBE_TIMEOUT_SECONDS


def bounded_exception_detail(prefix: str, exc: BaseException, *, limit: int = 400) -> str:
    """Format an exception without losing its type when ``str(exc)`` is empty."""
    detail = f"{prefix}: {type(exc).__name__}"
    message = str(exc).strip()
    if message:
        detail += f": {message}"
    return detail[:limit]


class IntegrityBackendRegistry(BackendRegistry):
    """Backend registry whose probe failures remain useful diagnostics."""

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
        except Exception as exc:  # noqa: BLE001 - provider probes are availability checks
            return BackendHealth(
                key=key,
                label=getattr(backend, "label", key),
                available=False,
                detail=bounded_exception_detail("health check failed", exc),
            )
