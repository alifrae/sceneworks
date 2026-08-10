"""Gemini CLI backend speaking ACP protocol version 1 over stdio.

This module is the ONLY place that knows about:
- the Gemini CLI executable and its flags;
- ACP / JSON-RPC message details;
- Gemini-specific event shapes.

Everything else in SceneWorks sees only the generic AgentBackend protocol and
the event vocabulary from app.events.types. Replacing Gemini with another
ACP-capable runtime (or dropping ACP for another protocol) means replacing
this one module and the registry wiring.

Protocol notes (verified against Gemini CLI 0.53.x, @agentclientprotocol/sdk v1):
- launch with `gemini --acp`; newline-delimited JSON-RPC 2.0 over stdio;
- agent methods: initialize (protocolVersion 1), session/new {cwd, mcpServers},
  session/prompt {sessionId, prompt}, session/cancel, session/close;
- client methods (agent asks the client): fs/read_text_file, fs/write_text_file,
  session/request_permission, terminal/create|output|wait_for_exit|kill|release;
- streaming: `session/update` notifications carrying update objects such as
  agent_message_chunk / agent_thought_chunk / tool_call / plan.

The file-system and terminal proxies mean every file read, write and command
the agent performs is mediated by SceneWorks, which enforces role permissions
(see AgentPolicy).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.agents.base import (
    AgentBackend,
    AgentEventSink,
    AgentRequest,
    AgentResult,
    BackendHealth,
    Workspace,
)
from app.config.settings import Settings

# ---------------------------------------------------------------- ACP client

_REQUEST_TIMEOUT = 120.0


class AcpError(Exception):
    pass


class AcpConnectionClosed(AcpError):
    pass


@dataclass
class AgentPolicy:
    """What the client proxy allows, derived from the role's permissions."""

    workspace_root: Path  # the worktree / cwd handed to the agent
    repo_root: Path | None = None
    allow_write: bool = False
    allow_shell: bool = False


@dataclass
class _Pending:
    future: asyncio.Future
    method: str


class _Terminal:
    def __init__(self, process: asyncio.subprocess.Process, sink: AgentEventSink):
        self.process = process
        self.sink = sink


class AcpStdioClient:
    """ACP v1 (client-owned) JSON-RPC client over stdio.

    The agent may send *requests to us* (fs/read_text_file, terminal/create,
    session/request_permission, ...). We answer them according to AgentPolicy â€”
    this is how SceneWorks enforces read-only Architect vs. write Engineer.
    """

    def __init__(
        self,
        launch_args: list[str],
        environment: dict[str, str],
        cwd: str,
        sink: AgentEventSink,
        policy: AgentPolicy,
    ):
        self._launch_args = launch_args
        self._environment = environment
        self._cwd = cwd
        self._sink = sink
        self._policy = policy
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[str, _Pending] = {}
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._next_id = 0
        self._eof = asyncio.Event()
        self._diagnostic_count = 0
        self._final_message: list[str] = []
        self._terminals: dict[str, _Terminal] = {}

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        args = self._launch_args
        env = {**os.environ, **self._environment}
        creationflags = 0
        if sys.platform == "win32":
            # Gemini CLI's shell tool needs a console to attach to on Windows
            # (its Bash tool fails with "AttachConsole failed" otherwise).
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE
            )
        try:
            self._process = await asyncio.create_subprocess_exec(
                *args,
                cwd=self._cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise AcpError(
                f"executable not found: {args[0] if args else '(none)'} "
                "(set SCENEWORKS_GEMINI_EXECUTABLE)"
            ) from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        for terminal in list(self._terminals.values()):
            await self._kill_terminal_process(terminal)
        self._terminals.clear()
        if self._process and self._process.returncode is None:
            await self._terminate_tree()

    async def _terminate_tree(self) -> None:
        proc = self._process
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
        if sys.platform == "win32" and proc.returncode is None:
            try:
                await asyncio.create_subprocess_exec(
                    "taskkill", "/pid", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError:
                pass

    async def _kill_terminal_process(self, terminal: _Terminal) -> None:
        proc = terminal.process
        if proc.returncode is not None:
            return
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass

    # ------------------------------------------------------------ IO helpers

    async def _read_loop(self) -> None:
        assert self._process is not None
        stream = self._process.stdout
        assert stream is not None
        try:
            while True:
                line = await stream.readline()
                if not line:
                    self._eof.set()
                    break
                line = line.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    await self._diagnostic(
                        "malformed JSON-RPC line received", {"line": line[:500]}
                    )
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self._eof.set()
            await self._diagnostic("ACP reader crashed", {"error": str(exc)})

    async def _drain_stderr(self) -> None:
        assert self._process is not None
        stream = self._process.stderr
        assert stream is not None
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text:
                    await self._diagnostic(
                        "gemini stderr", {"text": text[-2000:]}, severity="warning"
                    )
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive
            pass

    async def _diagnostic(self, message: str, payload: dict, severity: str = "warning") -> None:
        if self._diagnostic_count < 50:
            self._diagnostic_count += 1
            await self._sink.emit(
                "agent.event",
                {
                    "name": "diagnostic",
                    "message": message,
                    **payload,
                    "diagnostics": True,
                },
                severity=severity,
            )

    async def _handle_message(self, message: dict) -> None:
        if "id" in message and "method" not in message:
            # Response to one of our requests.
            pending = self._pending.pop(str(message["id"]), None)
            if pending:
                if "error" in message:
                    pending.future.set_exception(AcpError(str(message.get("error"))))
                else:
                    pending.future.set_result(message.get("result") or {})
        elif "id" in message and "method" in message:
            # Agent -> client request (fs proxy / permission / terminal).
            await self._handle_client_request(message)
        elif "method" in message:
            await self._handle_notification(message)
        else:  # pragma: no cover - defensive
            await self._diagnostic(
                "unrecognized JSON-RPC message", {"message": str(message)[:500]}
            )

    # -------------------------------------------- agent -> client requests

    async def _handle_client_request(self, message: dict) -> None:
        method = message.get("method", "")
        params = message.get("params") or {}
        request_id = message.get("id")
        try:
            if method == "fs/read_text_file":
                result = await self._serve_read_file(params)
            elif method == "fs/write_text_file":
                result = await self._serve_write_file(params)
            elif method == "session/request_permission":
                result = await self._serve_permission(params)
            elif method == "terminal/create":
                result = await self._serve_terminal_create(params)
            elif method == "terminal/output":
                result = await self._serve_terminal_output(params)
            elif method == "terminal/wait_for_exit":
                result = await self._serve_terminal_wait(params)
            elif method == "terminal/kill":
                result = await self._serve_terminal_kill(params)
            elif method == "terminal/release":
                result = await self._serve_terminal_kill(params)
            else:
                await self._diagnostic(
                    "unsupported client method requested by agent",
                    {"method": method, "params": str(params)[:300]},
                )
                await self._respond_error(request_id, f"unsupported client method: {method}")
                return
        except Exception as exc:  # noqa: BLE001 - never let a proxy failure kill the session
            await self._diagnostic(
                f"client method {method} failed", {"error": str(exc)[:500]}
            )
            await self._respond_error(request_id, f"{method} failed: {exc}")
            return
        await self._respond(request_id, result)

    async def _respond(self, request_id, result: dict) -> None:
        await self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _respond_error(self, request_id, error: str) -> None:
        await self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": error}})

    def _resolve_path(self, raw: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            path = self._policy.workspace_root / path
        return path.resolve()

    def _outside_reason(self, path: Path) -> str:
        """Explain why a path was rejected (helps the agent self-correct)."""
        repo = self._policy.repo_root
        if repo is not None and path.resolve().is_relative_to(repo.resolve()):
            return (
                f"{path} is in the human working tree, which agents may never "
                f"access. Use the pinned worktree at {self._policy.workspace_root}"
            )
        return f"{path} is outside the workspace {self._policy.workspace_root}"

    async def _serve_read_file(self, params: dict) -> dict:
        path = self._resolve_path(str(params.get("path") or ""))
        if not self._within_workspace(path):
            await self._sink.emit(
                "agent.event",
                {"name": "fs_read_denied", "path": str(path), "diagnostics": True},
                severity="warning",
            )
            raise AcpError(f"read denied: {self._outside_reason(path)}")
        data = path.read_text(encoding="utf-8", errors="replace")
        limit = params.get("limit")
        if isinstance(limit, int) and limit > 0:
            data = data[:limit]
        await self._sink.emit("agent.event", {"name": "file_read", "path": str(path)})
        return {"content": data}

    async def _serve_write_file(self, params: dict) -> dict:
        if not self._policy.allow_write:
            await self._sink.emit(
                "agent.event",
                {
                    "name": "fs_write_denied",
                    "path": str(params.get("path") or ""),
                    "message": "this role is read-only",
                    "diagnostics": True,
                },
                severity="warning",
            )
            raise AcpError("write denied: this role is read-only")
        path = self._resolve_path(str(params.get("path") or ""))
        if not self._within_workspace(path):
            await self._sink.emit(
                "agent.event",
                {"name": "fs_write_denied", "path": str(path), "diagnostics": True},
                severity="warning",
            )
            raise AcpError(f"write denied: {self._outside_reason(path)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(params.get("content") or ""), encoding="utf-8")
        await self._sink.emit("file.changed", {"path": str(path)})
        return {}

    async def _serve_permission(self, params: dict) -> dict:
        tool_call = params.get("toolCall") or {}
        kind = tool_call.get("kind") or "other"
        title = tool_call.get("title") or "tool"
        options = params.get("options") or []
        read_only_kinds = {"read", "search", "think", "fetch", "other"}
        allowed = self._policy.allow_write or kind in read_only_kinds
        if kind == "execute" and not self._policy.allow_shell:
            allowed = False
        if not allowed:
            await self._sink.emit(
                "agent.event",
                {"name": "permission_denied", "tool": title, "kind": kind, "diagnostics": True},
                severity="warning",
            )
        await self._sink.emit(
            "tool.started", {"tool": title, "kind": kind, "allowed": allowed}
        )
        if not allowed:
            # If a reject option exists, select it; otherwise cancel the request.
            for option in options:
                name = str(option.get("name") or "")
                if name in ("reject", "reject_once", "reject_always", "Deny", "Reject"):
                    return {"outcome": {"outcome": "selected", "optionId": option.get("optionId")}}
            return {"outcome": {"outcome": "cancelled"}}
        for option in options:
            name = str(option.get("name") or "")
            if name in ("allow", "allow_once", "allow_always", "Approve", "Allow"):
                return {"outcome": {"outcome": "selected", "optionId": option.get("optionId")}}
        if options:
            return {"outcome": {"outcome": "selected", "optionId": options[0].get("optionId")}}
        return {"outcome": {"outcome": "selected"}}

    async def _serve_terminal_create(self, params: dict) -> dict:
        if not self._policy.allow_shell:
            await self._sink.emit(
                "agent.event",
                {"name": "shell_denied", "command": str(params.get("command") or "")[:200], "diagnostics": True},
                severity="warning",
            )
            raise AcpError("shell access denied for this role")
        command = str(params.get("command") or "")
        requested_cwd = params.get("cwd")
        cwd = str(self._cwd)
        if requested_cwd:
            candidate = self._resolve_path(str(requested_cwd))
            if not self._within_workspace(candidate):
                await self._sink.emit(
                    "agent.event",
                    {"name": "shell_cwd_denied", "cwd": str(candidate), "diagnostics": True},
                    severity="warning",
                )
                raise AcpError(f"shell cwd denied: {self._outside_reason(candidate)}")
            cwd = str(candidate)
        args = [command, *(params.get("args") or [])]
        if sys.platform == "win32":
            shell = ["cmd.exe", "/d", "/s", "/c"] + [command, *(params.get("args") or [])]
            args = shell
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        terminal_id = f"term-{self._next_id}-{len(self._terminals)}"
        self._terminals[terminal_id] = _Terminal(proc, self._sink)
        await self._sink.emit(
            "command.started", {"command": " ".join(args), "terminal_id": terminal_id}
        )
        return {"terminalId": terminal_id}

    async def _serve_terminal_output(self, params: dict) -> dict:
        terminal = self._terminals.get(str(params.get("terminalId") or ""))
        if terminal is None:
            raise AcpError("unknown terminal")
        output = ""
        try:
            data = await asyncio.wait_for(terminal.process.stdout.read(64 * 1024), timeout=0.5)
            if data:
                output = data.decode(errors="replace")
        except asyncio.TimeoutError:
            pass
        if output:
            await self._sink.emit("command.output", {"output": output[-8000:]})
        exit_status = None
        if terminal.process.returncode is not None:
            exit_status = {"exitCode": terminal.process.returncode}
        return {"output": output, "truncated": False, "exitStatus": exit_status}

    async def _serve_terminal_wait(self, params: dict) -> dict:
        terminal = self._terminals.get(str(params.get("terminalId") or ""))
        if terminal is None:
            raise AcpError("unknown terminal")
        try:
            await asyncio.wait_for(terminal.process.wait(), timeout=_REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            await self._sink.emit(
                "agent.event",
                {"name": "terminal_timeout", "diagnostics": True},
                severity="warning",
            )
        return {"exitCode": terminal.process.returncode, "signal": None}

    async def _serve_terminal_kill(self, params: dict) -> dict:
        terminal_id = str(params.get("terminalId") or "")
        terminal = self._terminals.pop(terminal_id, None)
        if terminal is not None:
            await self._kill_terminal_process(terminal)
        return {}

    def _within_workspace(self, path: Path) -> bool:
        """Confine agent file access to the execution's workspace.

        The workspace is always a commit-pinned worktree. The main repository
        checkout (repo_root) is deliberately NOT allowed: it is the human's
        working tree and may hold uncommitted edits. Letting an agent read it
        would leak unreviewed state into a supposedly snapshot-pinned answer;
        letting an agent write it would modify the human's checkout.
        """
        resolved = path.resolve()
        return resolved.is_relative_to(self._policy.workspace_root.resolve())

    # -------------------------------------------------------- notifications

    async def _handle_notification(self, message: dict) -> None:
        method = message.get("method", "")
        params = message.get("params") or {}
        if method == "session/update":
            await self._map_session_update(params)
        elif method == "notifications/cancelled":
            await self._diagnostic("agent cancelled a request", {}, severity="warning")
        elif method == "notifications/progress":
            pass
        else:
            await self._sink.emit(
                "agent.event",
                {"name": method, "diagnostics": True, "params": str(params)[:500]},
            )

    async def _map_session_update(self, params: dict) -> None:
        update = params.get("update") or {}
        kind = update.get("sessionUpdate") or "unknown"
        if kind == "agent_message_chunk":
            text = _block_text(update.get("content"))
            if text:
                self._final_message.append(text)
                await self._sink.emit("agent.text_delta", {"delta": text})
        elif kind == "agent_thought_chunk":
            text = _block_text(update.get("content"))
            if text:
                await self._sink.emit("agent.thought_summary", {"text": text})
        elif kind == "tool_call":
            await self._sink.emit(
                "tool.started",
                {
                    "tool": update.get("title") or update.get("kind") or "tool",
                    "kind": update.get("kind") or "",
                    "status": update.get("status") or "pending",
                },
            )
        elif kind == "tool_call_update":
            status = update.get("status")
            if status in ("completed", "failed"):
                await self._sink.emit(
                    "tool.completed",
                    {
                        "tool": update.get("title") or "",
                        "kind": update.get("kind") or "",
                        "status": status,
                    },
                )
        elif kind == "plan":
            entries = update.get("entries") or []
            text = "\n".join(f"- [{e.get('status', '')}] {e.get('content', '')}" for e in entries)
            await self._sink.emit("agent.thought_summary", {"text": f"Plan:\n{text}"})
        elif kind == "user_message_chunk":
            pass
        else:
            await self._sink.emit(
                "agent.event",
                {"name": kind, "diagnostics": True, "payload": str(update)[:500]},
            )

    # ------------------------------------------------------------- requests

    async def _send(self, message: dict) -> None:
        if self._process is None or self._process.stdin is None or self._process.stdin.is_closing():
            raise AcpConnectionClosed("ACP process stdin is closed")
        line = json.dumps(message, ensure_ascii=False) + "\n"
        async with self._write_lock:
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float = _REQUEST_TIMEOUT) -> dict:
        if self._eof.is_set():
            raise AcpConnectionClosed("ACP process exited")
        self._next_id += 1
        message_id = str(self._next_id)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = _Pending(future, method)
        try:
            await self._send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise AcpError(f"ACP request {method} timed out") from exc
        except AcpConnectionClosed:
            self._pending.pop(message_id, None)
            raise


def _block_text(block) -> str:
    if isinstance(block, dict):
        if block.get("type") == "text":
            return str(block.get("text") or "")
        for value in block.values():
            text = _block_text(value)
            if text:
                return text
        return ""
    if isinstance(block, list):
        return " ".join(t for t in (_block_text(b) for b in block) if t).strip()
    return ""


# ------------------------------------------------------------------- backend


class GeminiACPBackend(AgentBackend):
    key = "gemini_acp"
    label = "Gemini CLI (ACP)"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cancel_events: dict[str, asyncio.Event] = {}

    def _executable(self) -> str | None:
        if self._settings.gemini_executable:
            return self._settings.gemini_executable
        return shutil.which("gemini")

    def _launch_command(self) -> list[str] | None:
        """Resolve the CLI wrapper (.CMD/.sh shims from npm) to the real
        executable. Launching node directly avoids orphaned child processes
        and Gemini's single-instance lock being held by a dead parent."""
        executable = self._executable()
        if not executable:
            return None
        path = Path(executable)
        if sys.platform == "win32" and path.suffix.lower() in (".cmd", ".bat") and path.is_file():
            resolved = self._resolve_windows_shim(path)
            if resolved:
                return resolved
        return [executable]

    @staticmethod
    def _resolve_windows_shim(path: Path) -> list[str] | None:
        """Parse an npm-generated .CMD shim: extract `node "<script>.js"`."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        match = re.search(r'"([^"]*node_modules[^"]*\.js)"', text)
        if not match:
            return None
        script = match.group(1).replace("%dp0%", str(path.parent))
        if not Path(script).is_file():
            return None
        node = shutil.which("node")
        if not node:
            return None
        return [node, script]

    async def health(self) -> BackendHealth:
        launch = self._launch_command()
        if not launch:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail="Gemini CLI not found on PATH (set SCENEWORKS_GEMINI_EXECUTABLE)",
            )
        executable = launch[0]
        try:
            proc = await asyncio.create_subprocess_exec(
                *launch,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._settings.gemini_startup_timeout_seconds
            )
            if proc.returncode != 0:
                return BackendHealth(
                    key=self.key,
                    label=self.label,
                    available=False,
                    detail=f"`{' '.join(launch)} --version` failed ({proc.returncode}): "
                    + (stderr.decode(errors="replace").strip() or "unknown error")[:300],
                )
            version = stdout.decode(errors="replace").strip().splitlines()
            version = version[0] if version else "unknown"
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=True,
                version=version,
                detail=(
                    f"executable: {executable}"
                    + (f", model: {self._settings.gemini_model}" if self._settings.gemini_model else ", model: auto")
                ),
            )
        except FileNotFoundError:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail=f"Gemini CLI executable not found: {executable}",
            )
        except (asyncio.TimeoutError, OSError) as exc:
            return BackendHealth(
                key=self.key,
                label=self.label,
                available=False,
                detail=f"health check failed: {exc}",
            )

    async def cancel(self, execution_id: str) -> None:
        event = self._cancel_events.get(execution_id)
        if event:
            event.set()

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult:
        launch = self._launch_command()
        if not launch:
            return AgentResult(
                status="failed",
                error="Gemini CLI not found on PATH "
                "(set SCENEWORKS_GEMINI_EXECUTABLE or install the Gemini CLI)",
            )
        cancel_event = asyncio.Event()
        self._cancel_events[request.execution_id] = cancel_event
        env = dict(self._settings.gemini_environment)
        if self._settings.gemini_model and "GEMINI_MODEL" not in env:
            env["GEMINI_MODEL"] = self._settings.gemini_model

        permissions = set(workspace.permissions)
        policy = AgentPolicy(
            workspace_root=Path(workspace.path),
            repo_root=Path(workspace.repo_path) if workspace.repo_path else None,
            allow_write="repository_write" in permissions,
            allow_shell="shell_execute" in permissions,
        )
        launch_args = launch + ["--acp"] + list(self._settings.gemini_extra_args)
        client = AcpStdioClient(
            launch_args=launch_args,
            environment=env,
            cwd=str(workspace.path),
            sink=event_sink,
            policy=policy,
        )
        session_id: str | None = None
        watcher: asyncio.Task | None = None

        async def watch_cancel() -> None:
            sink_wait = asyncio.create_task(event_sink.wait_for_cancel())
            backend_wait = asyncio.create_task(cancel_event.wait())
            try:
                await asyncio.wait(
                    [sink_wait, backend_wait], return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                sink_wait.cancel()
                backend_wait.cancel()

        try:
            await event_sink.emit(
                "agent.event",
                {
                    "name": "backend.starting",
                    "message": f"starting {' '.join(launch_args)} via ACP",
                    "diagnostics": True,
                },
            )
            await client.start()
            watcher = asyncio.create_task(watch_cancel())

            init_result = await client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "auth": {"terminal": False},
                        "fs": {"readTextFile": True, "writeTextFile": True},
                        "terminal": True,
                    },
                    "clientInfo": {"name": "SceneWorks", "version": "1.0"},
                },
                timeout=self._settings.gemini_startup_timeout_seconds,
            )
            await event_sink.emit(
                "agent.event",
                {
                    "name": "backend.initialized",
                    "message": f"ACP agent: {init_result.get('agentInfo', {}).get('title', 'unknown')}"
                    f" (protocol v{init_result.get('protocolVersion', '?')})",
                    "diagnostics": True,
                },
            )

            session_result = await client.request(
                "session/new",
                {"cwd": str(workspace.path), "mcpServers": []},
            )
            session_id = session_result.get("sessionId")

            # Compose the prompt: role instructions + the request.
            user_prompt = (
                f"# Role instructions\n{request.system_prompt}\n\n"
                f"# Request\n{request.user_prompt}"
            )
            prompt_task = asyncio.create_task(
                client.request(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": user_prompt}],
                    },
                    timeout=self._settings.execution_timeout_seconds + 30,
                )
            )
            while True:
                done, _ = await asyncio.wait(
                    {prompt_task, watcher}, return_when=asyncio.FIRST_COMPLETED
                )
                if prompt_task in done:
                    prompt_result = prompt_task.result()
                    break
                # Cancellation requested: tell the agent, wait briefly.
                await event_sink.emit(
                    "agent.event",
                    {"name": "cancel_requested", "message": "cancelling ACP prompt", "diagnostics": True},
                )
                try:
                    await client.request(
                        "session/cancel", {"sessionId": session_id}, timeout=10
                    )
                except (AcpError, AcpConnectionClosed):
                    pass
                prompt_task.cancel()
                try:
                    await prompt_task
                except (asyncio.CancelledError, AcpError):
                    pass
                return AgentResult(status="cancelled", error="cancelled by user")
            stop_reason = prompt_result.get("stopReason") or "end_turn"
            if stop_reason == "cancelled":
                return AgentResult(status="cancelled", error="cancelled by user")
            if stop_reason in ("refusal", "max_tokens", "max_turn_requests"):
                await event_sink.emit(
                    "agent.event",
                    {"name": "prompt_stopped", "stopReason": stop_reason, "diagnostics": True},
                    severity="warning",
                )
            return AgentResult(
                status="completed",
                summary=_join_text(client._final_message) or "Agent finished.",
            )
        except AcpConnectionClosed as exc:
            await event_sink.emit(
                "agent.event",
                {"name": "acp_connection_closed", "error": str(exc)},
                severity="error",
            )
            return AgentResult(status="failed", error=f"ACP connection closed: {exc}")
        except AcpError as exc:
            await event_sink.emit(
                "agent.event",
                {"name": "acp_error", "error": str(exc)[:2000]},
                severity="error",
            )
            return AgentResult(status="failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - adapter boundary must not crash the engine
            await event_sink.emit(
                "agent.event",
                {"name": "backend_error", "error": f"{type(exc).__name__}: {exc}"[:2000]},
                severity="error",
            )
            return AgentResult(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            if watcher:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass
            self._cancel_events.pop(request.execution_id, None)
            if session_id:
                for method, params in (
                    ("session/cancel", {"sessionId": session_id}),
                    ("session/close", {"sessionId": session_id}),
                ):
                    try:
                        await client.request(method, params, timeout=5)
                    except (AcpError, AcpConnectionClosed):
                        pass
            await client.stop()


def _join_text(parts: list[str]) -> str:
    """Reassemble streamed agent text.

    `agent_message_chunk` updates are *deltas* of one continuous message, not
    separate lines. Joining them with "\\n" inserted newlines at arbitrary
    chunk boundaries, which landed inside string literals and identifiers and
    corrupted any structured output — a triage reply split mid-token became
    `"use\\n_architect"` and failed to parse, silently discarding a correct
    routing decision. Concatenate them exactly as received.
    """
    return "".join(parts).strip()
