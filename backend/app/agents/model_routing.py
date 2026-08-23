"""Provider-neutral model-profile routing (WP8).

A role declares intent (`strongest`, `coding`, `research`). Configuration maps
that profile to an optional backend and model. Resolution happens when the
Execution is created, and the resolved backend/model are persisted on that
Execution so queued/restarted work cannot drift with later setting changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.config.settings import ModelProfileRoute, Settings
from app.roles.definitions import RoleDefinition


class ModelRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class ModelResolution:
    profile: str | None
    backend: str
    model: str | None
    source: str


class ModelRouter:
    def __init__(self, settings: Settings, available_backends: Iterable[str]):
        self._settings = settings
        self._available = frozenset(available_backends)

    def resolve(self, role: RoleDefinition) -> ModelResolution:
        profile = (role.model_profile or "").strip().lower() or None
        routes = {
            key.strip().lower(): value
            for key, value in self._settings.model_profile_routes.items()
            if key.strip()
        }
        route: ModelProfileRoute | None = routes.get(profile) if profile else None

        backend = (route.backend.strip() if route and route.backend else role.backend).strip()
        if not backend:
            raise ModelRoutingError(
                f"role {role.key!r} resolved to an empty backend"
            )
        if self._available and backend not in self._available:
            raise ModelRoutingError(
                f"model profile {profile or '<none>'!r} for role {role.key!r} "
                f"resolved to unregistered backend {backend!r}; available: "
                f"{', '.join(sorted(self._available))}"
            )

        if route is not None and route.model is not None:
            model = route.model.strip() or None
            source = "profile_route"
        else:
            model = self._backend_default_model(backend)
            source = "backend_default"

        return ModelResolution(
            profile=profile,
            backend=backend,
            model=model,
            source=source,
        )

    def _backend_default_model(self, backend: str) -> str | None:
        if backend == "gemini_acp":
            configured = self._settings.gemini_environment.get("GEMINI_MODEL")
            return (configured or self._settings.gemini_model or "").strip() or None
        if backend == "openhands":
            return (self._settings.openhands_model or "").strip() or None
        return None
