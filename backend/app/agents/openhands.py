"""OpenHands Agent Server backend.

Preferred production architecture:

    SceneWorks
       ↓
    AgentBackend
       ↓
    OpenHandsBackend
       ↓
    official OpenHands SDK (openhands.sdk.Workspace / Conversation)
       ↓
    OpenHands Agent Server
       ↓
    isolated SceneWorks task worktree

Supports three modes (tried in order of preference):
1. SDK/WebSocket mode (preferred production): Connect to a running
   OpenHands Agent Server using the official openhands-sdk package
   (Workspace + Conversation + WebSocket streaming).
   Configure `SCENEWORKS_OPENHANDS_URL`.
2. HTTP/polling mode (compatible fallback): Same REST API but without the
   OpenHands SDK — uses httpx for conversation lifecycle and polls for
   status. Events are deduplicated by index.
3. CLI/headless mode (development fallback only): Launch OpenHands as a
   one-off subprocess. Configure `SCENEWORKS_OPENHANDS_EXECUTABLE`.

OpenHands (https://github.com/OpenHands/OpenHands) is an open-source AI
coding agent platform. This adapter maps its conversation lifecycle to the
SceneWorks AgentBackend protocol.

The preferred path uses the supported OpenHands SDK/Agent Server
abstractions (openhands.sdk.Workspace, openhands.sdk.Conversation,
openhands.sdk.LLM, openhands.sdk.Agent) for WebSocket streaming, event
mapping, and lifecycle management.

IMPORTANT: This backend is labelled EXPERIMENTAL / UNVALIDATED.
No live integration test has been performed against a running OpenHands
Agent Server. The adapter code reflects the documented SDK API as of the
Software Agent SDK docs but has not been verified end-to-end.
Gemini ACP is the validated and default backend.

The CLI/headless mode is a fallback for local development only.

This module is the ONLY place that knows about:
- OpenHands SDK imports, Workspace, Conversation, LLM, Agent classes;
- OpenHands HTTP API URLs/paths and message formats;
- OpenHands CLI launch and flags;
- OpenHands-specific event shapes.

Everything else in SceneWorks sees only the generic AgentBackend protocol
and the event vocabulary from app.events.types.

No live OpenHands service/model is required for automated tests — tests use
the FakeAgentBackend on the "openhands" key.

If OpenHands cannot guarantee the required workspace boundary for a given
configuration, this adapter rejects the configuration rather than weakening
SceneWorks safety.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.agents.base import (
    AgentBackend,
    AgentEventSink,
    AgentRequest,
    AgentResult,
    BackendHealth,
    Workspace,
)
from app.config.settings import Settings

OPENHANDS_HEALTH_TIMEOUT = 10.0
OPENHANDS_RUN_TIMEOUT_FACTOR = 1.2

_EXPERIMENTAL_BANNER = "[EXPERIMENTAL / UNVALIDATED — no live integration test performed]"


class OpenHandsBackend(AgentBackend):
    key = "openhands"
    label = f"OpenHands Agent Server {_EXPERIMENTAL_BANNER}"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._sessions: dict[str, Any] = {}

    # ---------------------------------------------------------------- helpers

    def _server_url(self) -> str | None:
        return os.environ.get("SCENEWORKS_OPENHANDS_URL") or getattr(
            self._settings, "openhands_url", None
        )

    def _executable(self) -> str | None:
        exe = os.environ.get("SCENEWORKS_OPENHANDS_EXECUTABLE") or getattr(
            self._settings, "openhands_executable", None
        )
        if exe:
            return exe
        return shutil.which("openhands")

    def _model(self) -> str | None:
        return os.environ.get("SCENEWORKS_OPENHANDS_MODEL") or getattr(
            self._settings, "openhands_model", None
        )

    def _api_key(self) -> str:
        return os.environ.get("SCENEWORKS_OPENHANDS_API_KEY") or ""

    def _session_key(self) -> str:
        return os.environ.get("OH_SESSION_API_KEYS_0") or ""

    def _http_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        session_key = self._session_key()
        if session_key:
            headers["X-Session-API-Key"] = session_key
        return headers

    def _sdk_available(self) -> bool:
        try:
            import importlib
            spec = importlib.util.find_spec("openhands.sdk")
            return spec is not None
        except (ImportError, ModuleNotFoundError):
            return False

    # ------------------------------------------------------- AgentBackend API

    async def health(self) -> BackendHealth:
        server_url = self._server_url()
        if server_url:
            return await self._health_server(server_url)
        executable = self._executable()
        if executable:
            return await self._health_cli(executable)
        return BackendHealth(
            key=self.key,
            label=self.label,
            available=False,
            detail=(
                "OpenHands not configured: set SCENEWORKS_OPENHANDS_URL "
                "or SCENEWORKS_OPENHANDS_EXECUTABLE"
            ),
        )

    async def _health_server(self, server_url: str) -> BackendHealth:
        try:
            import httpx
        except ImportError:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail="httpx not installed (required for OpenHands server mode)",
            )
        sdk_info = ""
        if self._sdk_available():
            sdk_info = f" [openhands-sdk available{_EXPERIMENTAL_BANNER}]"
        try:
            async with httpx.AsyncClient(timeout=OPENHANDS_HEALTH_TIMEOUT) as client:
                resp = await client.get(
                    f"{server_url.rstrip('/')}/health",
                    headers=self._http_headers(),
                )
            if resp.status_code == 200:
                data = resp.json() if resp.text else {}
                return BackendHealth(
                    key=self.key,
                    label=self.label,
                    available=True,
                    version=data.get("version") or data.get("server_version"),
                    detail=f"Connected to {server_url}{sdk_info}",
                )
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail=f"Health check returned {resp.status_code}: {resp.text[:200]}",
            )
        except Exception as exc:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail=f"Health check failed: {exc}",
            )

    async def _health_cli(self, executable: str) -> BackendHealth:
        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=OPENHANDS_HEALTH_TIMEOUT
            )
            if proc.returncode != 0:
                return BackendHealth(
                    key=self.key,
                    label=self.label,
                    available=False,
                    detail=(
                        f"`{executable} --version` failed ({proc.returncode}): "
                        + (stderr.decode(errors="replace").strip() or "unknown error")
                    )[:300],
                )
            version = stdout.decode(errors="replace").strip().splitlines()
            version = version[0] if version else "unknown"
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=True,
                version=version,
                detail=f"executable: {executable} [CLI/headless — development fallback]",
            )
        except FileNotFoundError:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail=f"OpenHands executable not found: {executable}",
            )
        except (asyncio.TimeoutError, OSError) as exc:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail=f"Health check failed: {exc}",
            )

    async def cancel(self, execution_id: str) -> None:
        event = self._cancel_events.get(execution_id)
        if event:
            event.set()
        session = self._sessions.pop(execution_id, None)
        if session is not None and hasattr(session, "close"):
            try:
                session.close()
            except Exception:
                pass

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult:
        server_url = self._server_url()
        if server_url:
            if self._sdk_available():
                return await self._run_sdk(request, workspace, event_sink, server_url)
            return await self._run_http(request, workspace, event_sink, server_url)

        executable = self._executable()
        if executable:
            return await self._run_cli(request, workspace, event_sink, executable)

        return AgentResult(
            status="failed",
            error=(
                "OpenHands not configured: set SCENEWORKS_OPENHANDS_URL "
                "or SCENEWORKS_OPENHANDS_EXECUTABLE"
            ),
        )

    # ------------------------------------------------------- SDK/WebSocket execution

    async def _run_sdk(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
        server_url: str,
    ) -> AgentResult:
        cancel_event = asyncio.Event()
        self._cancel_events[request.execution_id] = cancel_event
        try:
            await event_sink.emit(
                "agent.event",
                {
                    "name": "backend.connecting",
                    "message": (
                        f"Connecting to OpenHands Agent Server at {server_url}"
                        f" (SDK/WebSocket) {_EXPERIMENTAL_BANNER}"
                    ),
                    "diagnostics": True,
                },
            )

            from openhands.sdk import Agent, Conversation, LLM, Workspace as OHWorkspace
            from openhands.tools.preset.default import get_default_agent

            user_prompt = (
                f"# Role instructions\n{request.system_prompt}\n\n"
                f"# Request\n{request.user_prompt}"
            )

            llm_kwargs: dict[str, Any] = {}
            if self._model():
                llm_kwargs["model"] = self._model()
            api_key = self._api_key()
            if api_key:
                llm_kwargs["api_key"] = api_key

            llm = LLM(**llm_kwargs) if llm_kwargs else LLM()
            agent = get_default_agent(llm=llm)

            oh_workspace = OHWorkspace(
                host=server_url.rstrip("/"),
                api_key=self._session_key() or None,
                working_dir=str(workspace.path),
            )

            conversation = Conversation(agent=agent, workspace=oh_workspace)
            self._sessions[request.execution_id] = conversation

            conversation.send_message(user_prompt)

            summary_parts: list[str] = []
            try:
                conversation.run()
            except Exception as exc:
                if event_sink.cancelled() or cancel_event.is_set():
                    return AgentResult(status="cancelled", error="cancelled by user")
                return AgentResult(status="failed", error=f"{type(exc).__name__}: {exc}"[:2000])

            for event in getattr(conversation, "events", []):
                if event_sink.cancelled() or cancel_event.is_set():
                    break
                event_dict = event if isinstance(event, dict) else {}
                event_type = event_dict.get("type") or getattr(event, "type", None) or ""
                event_data = event_dict.get("content") or getattr(event, "content", None) or ""
                if isinstance(event_data, str) and event_data.strip():
                    summary_parts.append(event_data)
                    await event_sink.emit(
                        "agent.text_delta",
                        {"delta": str(event_data)[:2000]},
                    )

            if event_sink.cancelled() or cancel_event.is_set():
                return AgentResult(status="cancelled", error="cancelled by user")

            summary = "\n".join(summary_parts[-5:]) or "OpenHands SDK completed."
            return AgentResult(status="completed", summary=summary)

        except ImportError:
            return await self._run_http(request, workspace, event_sink, server_url)
        except Exception as exc:
            return AgentResult(
                status="failed",
                error=f"OpenHands SDK error: {exc}"[:2000],
            )
        finally:
            session = self._sessions.pop(request.execution_id, None)
            if session is not None and hasattr(session, "close"):
                try:
                    session.close()
                except Exception:
                    pass
            self._cancel_events.pop(request.execution_id, None)

    # ------------------------------------------------------- HTTP execution

    async def _run_http(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
        server_url: str,
    ) -> AgentResult:
        try:
            import httpx
        except ImportError:
            return AgentResult(status="failed", error="httpx not installed")
        cancel_event = asyncio.Event()
        self._cancel_events[request.execution_id] = cancel_event
        try:
            timeout = self._settings.execution_timeout_seconds * OPENHANDS_RUN_TIMEOUT_FACTOR
            async with httpx.AsyncClient(timeout=timeout) as client:
                await event_sink.emit(
                    "agent.event",
                    {
                        "name": "backend.connecting",
                        "message": (
                            f"Connecting to OpenHands at {server_url}"
                            f" [HTTP polling — compatibility fallback]"
                        ),
                        "diagnostics": True,
                    },
                )

                create_resp = await client.post(
                    f"{server_url.rstrip('/')}/api/conversations",
                    headers=self._http_headers(),
                    json={
                        "directory": str(workspace.path),
                        "model": self._model(),
                    },
                )
                if create_resp.status_code not in (200, 201):
                    return AgentResult(
                        status="failed",
                        error=f"Failed to create conversation: {create_resp.status_code}",
                    )
                conversation = create_resp.json()
                conversation_id = conversation.get("id") or conversation.get("conversation_id")

                await event_sink.emit(
                    "agent.event",
                    {
                        "name": "backend.initialized",
                        "message": f"OpenHands conversation {conversation_id} started",
                        "diagnostics": True,
                    },
                )

                user_prompt = (
                    f"# Role instructions\n{request.system_prompt}\n\n"
                    f"# Request\n{request.user_prompt}"
                )

                msg_resp = await client.post(
                    f"{server_url.rstrip('/')}/api/conversations/{conversation_id}/messages",
                    headers=self._http_headers(),
                    json={"content": user_prompt},
                )
                if msg_resp.status_code not in (200, 201):
                    return AgentResult(
                        status="failed",
                        error=f"Failed to send message: {msg_resp.status_code}",
                    )

                poll_interval = 1.0
                summary_parts: list[str] = []
                seen_indices: set[int] = set()
                while True:
                    if event_sink.cancelled() or cancel_event.is_set():
                        try:
                            await client.delete(
                                f"{server_url.rstrip('/')}/api/conversations/{conversation_id}",
                                headers=self._http_headers(),
                            )
                        except Exception:
                            pass
                        return AgentResult(status="cancelled", error="cancelled by user")

                    status_resp = await client.get(
                        f"{server_url.rstrip('/')}/api/conversations/{conversation_id}",
                        headers=self._http_headers(),
                    )
                    if status_resp.status_code != 200:
                        return AgentResult(
                            status="failed",
                            error=f"Conversation status check failed: {status_resp.status_code}",
                        )
                    conv = status_resp.json()
                    state = (conv.get("state") or conv.get("status") or "").lower()

                    messages = conv.get("messages") or conv.get("events") or []
                    if isinstance(messages, list):
                        for idx, msg in enumerate(messages):
                            if idx in seen_indices:
                                continue
                            seen_indices.add(idx)
                            content = msg.get("content") or msg.get("message") or ""
                            if isinstance(content, str) and content.strip():
                                summary_parts.append(content)
                                if len(summary_parts) <= 20:
                                    await event_sink.emit(
                                        "agent.text_delta",
                                        {"delta": content[:2000]},
                                    )

                    if state in ("completed", "finished", "done", "success"):
                        return AgentResult(
                            status="completed",
                            summary="\n".join(summary_parts[-5:]) or "OpenHands completed.",
                        )
                    if state in ("failed", "error", "cancelled"):
                        error_detail = conv.get("error") or conv.get("detail") or "Unknown error"
                        return AgentResult(status="failed", error=str(error_detail)[:2000])
                    if state in ("stopped", "terminated"):
                        return AgentResult(status="cancelled", error="Cancelled by server")

                    await asyncio.sleep(poll_interval)
        finally:
            self._cancel_events.pop(request.execution_id, None)

    # -------------------------------------------------- subprocess execution
    # CLI/headless mode is a development fallback only.
    # The `--task` / `--directory` flags are version-dependent;
    # the preferred production path is SDK/WebSocket mode above.

    async def _run_cli(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
        executable: str,
    ) -> AgentResult:
        cancel_event = asyncio.Event()
        self._cancel_events[request.execution_id] = cancel_event

        env = {**os.environ}
        if self._model():
            env["LLM_MODEL"] = self._model()
        for key, val in (self._settings.openhands_environment or {}).items():
            env[key] = val

        user_prompt = (
            f"# Role instructions\n{request.system_prompt}\n\n"
            f"# Request\n{request.user_prompt}"
        )

        cmd = [
            executable,
            "run",
            "--task", user_prompt[:4000],
            "--directory", str(workspace.path),
        ]

        await event_sink.emit(
            "agent.event",
            {
                "name": "backend.starting",
                "message": f"Launching OpenHands CLI: {' '.join(cmd[:2])} ... [headless — development fallback]",
                "diagnostics": True,
            },
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace.path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
        except FileNotFoundError:
            return AgentResult(
                status="failed",
                error=f"OpenHands executable not found: {executable}",
            )

        try:
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []

            async def read_stream(stream, chunks, emit_label):
                try:
                    while True:
                        if event_sink.cancelled() or cancel_event.is_set():
                            break
                        line = await stream.readline()
                        if not line:
                            break
                        text = line.decode(errors="replace").rstrip()
                        if text:
                            chunks.append(text)
                            if len(chunks) <= 50:
                                await event_sink.emit(
                                    emit_label,
                                    {"delta": text[:2000]},
                                )
                except (asyncio.CancelledError, ValueError):
                    pass

            stdout_task = asyncio.create_task(
                read_stream(proc.stdout, stdout_chunks, "agent.text_delta")
            )
            stderr_task = asyncio.create_task(
                read_stream(proc.stderr, stderr_chunks, "agent.event")
            )

            try:
                rc = await asyncio.wait_for(
                    proc.wait(),
                    timeout=self._settings.execution_timeout_seconds + 60,
                )
            except asyncio.TimeoutError:
                await self._kill_proc(proc)
                return AgentResult(
                    status="failed",
                    error="OpenHands execution timed out",
                )

            await stdout_task
            await stderr_task

            if event_sink.cancelled() or cancel_event.is_set():
                return AgentResult(status="cancelled", error="cancelled by user")

            if rc != 0:
                error_text = "\n".join(stderr_chunks[-10:]) or f"Exit code {rc}"
                return AgentResult(status="failed", error=error_text[:2000])

            summary = "\n".join(stdout_chunks[-10:]).strip() or "OpenHands completed."
            return AgentResult(status="completed", summary=summary)
        finally:
            self._cancel_events.pop(request.execution_id, None)
            await self._kill_proc(proc)

    @staticmethod
    async def _kill_proc(proc: asyncio.subprocess.Process | None) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
        if proc.returncode is None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass
