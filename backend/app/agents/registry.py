"""Backend registry: the only place backends are constructed and looked up."""

from __future__ import annotations

from app.agents.base import AgentBackend, BackendHealth
from app.agents.fake import FakeAgentBackend
from app.agents.gemini_acp import GeminiACPBackend
from app.config.settings import Settings


class BackendNotFoundError(KeyError):
    pass


class BackendRegistry:
    def __init__(self, settings: Settings, include_fake: bool = True):
        self._backends: dict[str, AgentBackend] = {
            "gemini_acp": GeminiACPBackend(settings),
        }
        if include_fake:
            self._backends["fake"] = FakeAgentBackend()

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

    async def health_all(self) -> list[BackendHealth]:
        return [await backend.health() for backend in self._backends.values()]
