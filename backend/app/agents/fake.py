"""Scripted fake backend for tests, demos and qualification.

Never talks to a real model. Useful for validating the full workflow
(state machine, worktrees, events, UI) without Gemini access.

Two scripting modes:

- ``steps``: one flat script applied to every role. Simple, and what most
  workflow tests want.
- ``role_scripts``: a per-role script, so one run can give Triage a routing
  decision, the Engineer a set of edits, and the Reviewer a verdict. The
  qualification suite (``backend/evaluation``) needs this to evaluate routing
  and review outcomes independently of each other.
- ``role_sequences``: a per-role *list* of scripts, consumed one per invocation.
  Needed to script a Reviewer that requests changes and then approves the
  repair, which happens inside a single graph run so nothing outside can swap
  the backend between the two calls. The final script repeats once the sequence
  is exhausted.

Resolution order for a role: ``role_sequences`` → ``role_scripts`` → ``steps``.

Triage is special-cased: it must return parseable JSON or the workflow
degrades to default routing. When no explicit triage script is given, this
backend answers with DEFAULT_TRIAGE_DECISION so that the Triage node exercises
its real parse-and-route path instead of being bypassed.
"""

from __future__ import annotations

import asyncio
import json
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

# The routing the workflow used to hard-code for the fake backend. Kept as the
# default so existing tests observe identical behaviour now that Triage
# actually runs.
DEFAULT_TRIAGE_DECISION = {
    "request_type": "feature",
    "use_product": False,
    "use_cto": False,
    "use_architect": True,
    "use_technical_expert": False,
    "requires_implementation": True,
    "reasoning_summary": "fake backend default routing; architect path",
}


def triage_summary(**overrides) -> str:
    """Build a triage JSON payload, overriding individual fields.

    ``triage_summary(requires_implementation=False)`` gives a decision that
    routes an investigation away from the Engineer.
    """
    decision = {**DEFAULT_TRIAGE_DECISION, **overrides}
    return json.dumps(decision)


@dataclass
class ScriptStep:
    kind: str  # "emit" | "file" | "delete" | "commit" | "sleep" | "fail" | "summary"
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

    def __init__(
        self,
        steps: list[ScriptStep] | None = None,
        role_scripts: dict[str, list[ScriptStep]] | None = None,
        role_sequences: dict[str, list[list[ScriptStep]]] | None = None,
    ):
        self.steps = steps or [
            ScriptStep(kind="emit", type="agent.message", payload={"text": "Fake backend starting."}),
            ScriptStep(kind="sleep", seconds=0.1),
            ScriptStep(kind="summary"),
        ]
        self.role_scripts = dict(role_scripts or {})
        self.role_sequences = {k: list(v) for k, v in (role_sequences or {}).items()}
        #: Invocation count per role, so the qualification harness can assert how
        #: many times a role actually ran.
        self.invocations: dict[str, int] = {}
        self._cancels: dict[str, asyncio.Event] = {}

    def script_for(self, role: str) -> list[ScriptStep]:
        """Resolve the script for one role invocation."""
        index = self.invocations.get(role, 0)
        self.invocations[role] = index + 1

        sequence = self.role_sequences.get(role)
        if sequence:
            # Clamp to the last entry so an unexpected extra invocation reuses
            # the final script instead of raising deep inside the graph.
            return sequence[min(index, len(sequence) - 1)]
        if role in self.role_scripts:
            return self.role_scripts[role]
        if role == "triage":
            # Without this, a flat script's prose summary reaches the triage
            # parser, which fails and degrades routing. Answering with valid
            # JSON keeps the real triage path exercised.
            return [ScriptStep(kind="summary", summary=triage_summary())]
        return self.steps

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult:
        self._cancels[request.execution_id] = asyncio.Event()
        summary = "fake backend completed"
        try:
            for step in self.script_for(request.role):
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
                elif step.kind == "delete":
                    # Needed to script refactors that move code out of a file.
                    path = Path(workspace.path) / (step.path or "")
                    if path.is_file():
                        path.unlink()
                        await event_sink.emit(
                            "file.changed",
                            {"path": str(path.relative_to(workspace.path)),
                             "deleted": True},
                        )
                elif step.kind == "commit":
                    from app.git.workspace import run_git

                    await run_git(Path(workspace.path), "add", "-A")
                    # A repair iteration that rewrites identical content has
                    # nothing to commit. `git commit` exits 1 there, which would
                    # crash the scripted run over a fixture detail rather than a
                    # SceneWorks defect — so skip instead.
                    staged = await run_git(
                        Path(workspace.path), "status", "--porcelain"
                    )
                    if not staged.strip():
                        await event_sink.emit(
                            "agent.message",
                            {"text": "nothing to commit; worktree already matches"},
                        )
                        continue
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
