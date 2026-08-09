"""Scripted fake backend for tests and demos.

Never talks to a real model. Useful for validating the full workflow
(state machine, worktrees, events, UI) without Gemini access.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.base import (
    AgentBackend,
    AgentEventSink,
    AgentRequest,
    AgentResult,
    BackendHealth,
    Workspace,
)


@dataclass
class ScriptStep:
    kind: str  # "emit" | "file" | "commit" | "sleep" | "fail" | "summary"
    type: str | None = None
    payload: dict = field(default_factory=dict)
    path: str | None = None
    content: str = ""
    message: str = "task implementation"
    seconds: float = 0.05
    error: str | None = None
    summary: str = "fake backend completed"


class FakeAgentBackend(AgentBackend):
    key = "fake"
    label = "Fake (scripted)"

    def __init__(self, steps: list[ScriptStep] | None = None):
        self.steps = steps or [
            ScriptStep(kind="emit", type="agent.message", payload={"text": "Fake backend starting."}),
            ScriptStep(kind="sleep", seconds=0.1),
            ScriptStep(kind="summary"),
        ]
        self._cancels: dict[str, asyncio.Event] = {}

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult:
        self._cancels[request.execution_id] = asyncio.Event()
        summary = "fake backend completed"
        try:
            for step in self.steps:
                if event_sink.cancelled():
                    return AgentResult(status="cancelled", summary=None)
                if step.kind == "emit":
                    await event_sink.emit(step.type or "agent.event", step.payload)
                elif step.kind == "file":
                    path = Path(workspace.path) / (step.path or "change.txt")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(step.content, encoding="utf-8")
                    await event_sink.emit(
                        "file.changed",
                        {"path": str(path.relative_to(workspace.path))},
                    )
                elif step.kind == "commit":
                    from app.git.workspace import run_git

                    await run_git(Path(workspace.path), "add", "-A")
                    await run_git(Path(workspace.path), "commit", "-m", step.message)
                    commit = (await run_git(Path(workspace.path), "rev-parse", "HEAD")).strip()
                    await event_sink.emit(
                        "git.commit", {"commit": commit, "message": step.message}
                    )
                elif step.kind == "sleep":
                    await asyncio.sleep(step.seconds)
                elif step.kind == "fail":
                    await event_sink.emit("execution.failed", {"error": step.error})
                    return AgentResult(status="failed", error=step.error)
                elif step.kind == "summary":
                    summary = step.summary
            return AgentResult(status="completed", summary=summary)
        finally:
            self._cancels.pop(request.execution_id, None)

    async def cancel(self, execution_id: str) -> None:
        self._cancels.get(execution_id, asyncio.Event()).set()

    async def health(self) -> BackendHealth:
        return BackendHealth(key=self.key, label=self.label, available=True, version="fake-1.0")
