"""OpenCode headless CLI AgentBackend (WP14).

This adapter deliberately uses ``opencode run`` rather than ACP. It provides a
second coding-agent transport and can use any provider/model configured in
OpenCode. SceneWorks still owns the Git worktree and execution provenance.

Policy note: OpenCode's headless auto-approval is not equivalent to Gemini ACP's
per-tool SceneWorks mediation. WP14 therefore qualifies this backend for
write-capable coding/delegation work only. Read-only roles must use a backend
with enforceable read-only tooling until an OpenCode policy adapter is added.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from app.agents.base import (
    AgentEventSink,
    AgentRequest,
    AgentResult,
    BackendHealth,
    Workspace,
)
from app.config.settings import Settings


class OpenCodeBackend:
    key = "opencode"
    label = "OpenCode CLI"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._active: dict[str, asyncio.subprocess.Process] = {}

    def _executable(self) -> str | None:
        configured = self._settings.opencode_executable
        if configured:
            # `which` also validates an explicit absolute/relative executable
            # path instead of treating any configured string as healthy.
            return shutil.which(configured)
        return shutil.which("opencode")

    def _model(self, request: AgentRequest | None = None) -> str | None:
        if request is not None and request.model:
            return request.model
        return self._settings.opencode_model

    async def health(self) -> BackendHealth:
        executable = self._executable()
        if not executable:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail="opencode executable not found; install OpenCode or configure SCENEWORKS_OPENCODE_EXECUTABLE",
            )
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self._settings.opencode_environment},
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        except Exception as exc:  # noqa: BLE001
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail=f"OpenCode probe failed: {type(exc).__name__}: {exc}"[:400],
            )
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip() or "version probe failed"
            return BackendHealth(
                key=self.key, label=self.label, available=False, detail=detail[:400]
            )
        version = stdout.decode(errors="replace").strip() or None
        model = self._settings.opencode_model or "OpenCode configured/default model"
        return BackendHealth(
            key=self.key,
            label=self.label,
            available=True,
            version=version,
            detail=f"headless CLI transport; model={model}; write-capable roles only in WP14",
        )

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult:
        executable = self._executable()
        if not executable:
            return AgentResult(status="failed", error="OpenCode executable not found")
        permissions = set(workspace.permissions or ())
        if "repository_write" not in permissions:
            return AgentResult(
                status="failed",
                error=(
                    "OpenCode WP14 backend is restricted to write-capable coding/delegation "
                    "roles because headless --auto does not provide SceneWorks per-tool read-only enforcement"
                ),
            )

        cwd = Path(workspace.path).resolve()
        if not cwd.is_dir():
            return AgentResult(status="failed", error=f"workspace does not exist: {cwd}")

        prompt = request.user_prompt
        if request.system_prompt:
            prompt = f"{request.system_prompt}\n\n--- TASK ---\n{request.user_prompt}"

        args = ["run", "--auto", "--dir", str(cwd)]
        if model := self._model(request):
            args.extend(["--model", model])
        if self._settings.opencode_agent:
            args.extend(["--agent", self._settings.opencode_agent])
        args.extend(self._settings.opencode_extra_args)
        args.append(prompt)

        await event_sink.emit(
            "agent.event",
            {
                "name": "backend.mode",
                "backend": self.key,
                "transport": "opencode run",
                "model": self._model(request),
                "cwd": "engineering worktree",
            },
        )
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self._settings.opencode_environment},
            )
        except OSError as exc:
            return AgentResult(status="failed", error=f"could not start OpenCode: {exc}")

        self._active[request.execution_id] = process
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        async def drain(reader: asyncio.StreamReader | None, stream: str, target: list[str]) -> None:
            if reader is None:
                return
            while True:
                line = await reader.readline()
                if not line:
                    return
                text = line.decode(errors="replace")
                target.append(text)
                await event_sink.emit(
                    "command.output",
                    {"stream": stream, "text": text[:8000], "backend": self.key},
                )

        readers = [
            asyncio.create_task(drain(process.stdout, "stdout", stdout_parts)),
            asyncio.create_task(drain(process.stderr, "stderr", stderr_parts)),
        ]
        cancel_waiter = asyncio.create_task(event_sink.wait_for_cancel())
        process_waiter = asyncio.create_task(process.wait())
        try:
            done, _ = await asyncio.wait(
                {cancel_waiter, process_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_waiter in done and event_sink.cancelled() and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            await asyncio.gather(*readers, return_exceptions=True)
        finally:
            cancel_waiter.cancel()
            process_waiter.cancel()
            self._active.pop(request.execution_id, None)

        stdout = "".join(stdout_parts).strip()
        stderr = "".join(stderr_parts).strip()
        if event_sink.cancelled():
            return AgentResult(status="cancelled", error="OpenCode execution cancelled")
        if process.returncode != 0:
            detail = stderr or stdout or f"OpenCode exited with {process.returncode}"
            return AgentResult(status="failed", error=detail[-4000:])
        return AgentResult(status="completed", summary=stdout[-12000:] or "OpenCode completed")

    async def cancel(self, execution_id: str) -> None:
        process = self._active.get(execution_id)
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
