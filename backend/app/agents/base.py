"""AgentBackend abstraction.

SceneWorks owns this abstraction. A backend is *any* way of fulfilling a
role invocation: an autonomous coding agent runtime (Gemini CLI via ACP,
Claude Code, Codex) or, in the future, a direct LLM API without
filesystem/shell capabilities.

Backends must not leak protocol-specific types, model names, or process
details beyond this module. Event payloads they emit must use the generic
event vocabulary in app.events.types.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal, Protocol


@dataclass
class Workspace:
    """The sandboxed working area for one execution."""

    path: Path  # cwd for the agent (a worktree, or a read-only repo checkout)
    repo_path: Path
    branch: str | None = None
    base_commit: str | None = None
    permissions: tuple[str, ...] = ()


@dataclass
class AgentRequest:
    execution_id: str
    role: str
    system_prompt: str
    user_prompt: str
    model_profile: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    status: Literal["completed", "cancelled", "failed"]
    summary: str | None = None
    error: str | None = None


@dataclass
class BackendHealth:
    key: str
    label: str
    available: bool
    version: str | None = None
    detail: str | None = None


class AgentEventSink:
    """Per-execution channel into SceneWorks event storage + streaming.

    Backends call emit() for anything user-visible. `cancelled()` lets a
    backend poll for cancellation while it is waiting on I/O.
    """

    def __init__(
        self,
        execution_id: str,
        task_id: int | None,
        emit_callback: Callable[[str, dict], Awaitable[None]],
    ):
        self.execution_id = execution_id
        self.task_id = task_id
        self._emit_callback = emit_callback
        self._cancel_event = asyncio.Event()

    async def emit(self, type: str, payload: dict, severity: str = "info") -> None:
        await self._emit_callback(type, payload, severity)

    def cancel(self) -> None:
        self._cancel_event.set()

    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    async def wait_for_cancel(self) -> None:
        await self._cancel_event.wait()


class AgentBackend(Protocol):
    key: str
    label: str

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult: ...

    async def cancel(self, execution_id: str) -> None: ...

    async def health(self) -> BackendHealth: ...
