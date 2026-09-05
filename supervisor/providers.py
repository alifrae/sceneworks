from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib import request

from supervisor.model import ComponentKey, ProcessObservation


class ProcessProvider(Protocol):
    def observe(self, component: ComponentKey) -> ProcessObservation: ...

    def start(self, component: ComponentKey) -> None: ...

    def stop(self, component: ComponentKey) -> None: ...


class HealthProvider(Protocol):
    def healthy(self, component: ComponentKey) -> bool: ...


class FakeProcessProvider:
    def __init__(self) -> None:
        self._observations: dict[ComponentKey, ProcessObservation] = {}
        self.calls: list[tuple[str, ComponentKey]] = []

    def set_process(
        self,
        component: ComponentKey,
        *,
        running: bool,
        owned: bool,
        pid: int | None = None,
    ) -> None:
        self._observations[component] = ProcessObservation(
            running=running,
            owned=owned,
            pid=pid,
        )

    def observe(self, component: ComponentKey) -> ProcessObservation:
        return self._observations.get(
            component,
            ProcessObservation(running=False, owned=False, pid=None),
        )

    def start(self, component: ComponentKey) -> None:
        self.calls.append(("start", component))
        current = self.observe(component)
        self._observations[component] = ProcessObservation(
            running=True,
            owned=True,
            pid=current.pid or (1000 + list(ComponentKey).index(component)),
        )

    def stop(self, component: ComponentKey) -> None:
        self.calls.append(("stop", component))
        current = self.observe(component)
        self._observations[component] = ProcessObservation(
            running=False,
            owned=True,
            pid=current.pid,
        )


class FakeHealthProvider:
    def __init__(self) -> None:
        self._healthy: dict[ComponentKey, bool] = defaultdict(bool)
        self._queued: dict[ComponentKey, deque[bool]] = defaultdict(deque)

    def set_healthy(self, component: ComponentKey, healthy: bool) -> None:
        self._healthy[component] = healthy

    def queue_results(self, component: ComponentKey, results: list[bool]) -> None:
        self._queued[component].extend(results)

    def healthy(self, component: ComponentKey) -> bool:
        queued = self._queued[component]
        if queued:
            return queued.popleft()
        return self._healthy[component]


@dataclass(frozen=True)
class HttpProbe:
    url: str
    timeout_seconds: float = 1.0


class HttpHealthProvider:
    def __init__(self, probes: dict[ComponentKey, HttpProbe]) -> None:
        self._probes = probes

    def healthy(self, component: ComponentKey) -> bool:
        probe = self._probes[component]
        try:
            with request.urlopen(probe.url, timeout=probe.timeout_seconds) as response:
                return 200 <= int(response.status) < 400
        except Exception:
            return False


@dataclass(frozen=True)
class FixedLaunch:
    argv: tuple[str, ...]
    cwd: Path


__all__ = [
    "FakeHealthProvider",
    "FakeProcessProvider",
    "FixedLaunch",
    "HealthProvider",
    "HttpHealthProvider",
    "HttpProbe",
    "ProcessProvider",
]
