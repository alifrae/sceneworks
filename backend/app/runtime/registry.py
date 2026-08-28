"""Execution runtime registry (WP14)."""

from __future__ import annotations

from app.runtime.base import ExecutionRuntime, RuntimeErrorBase
from app.runtime.native import NativeRuntime


class RuntimeNotFoundError(RuntimeErrorBase):
    pass


class RuntimeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[str, ExecutionRuntime] = {"native": NativeRuntime()}

    def get(self, key: str) -> ExecutionRuntime:
        try:
            return self._runtimes[key]
        except KeyError as exc:
            raise RuntimeNotFoundError(
                f"runtime {key!r} is not registered (available: {', '.join(sorted(self._runtimes))})"
            ) from exc

    def keys(self) -> list[str]:
        return sorted(self._runtimes)

    async def shutdown(self) -> None:
        for runtime in self._runtimes.values():
            await runtime.shutdown()
