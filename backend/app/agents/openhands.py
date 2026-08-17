"""OpenHands Agent SDK backend.

    SceneWorks -> AgentBackend -> OpenHandsBackend -> openhands-sdk
                                                   -> LocalWorkspace  (in-process)
                                                   or RemoteWorkspace (Agent Server)
                                                   -> pinned SceneWorks worktree

This module is the ONLY place that knows about OpenHands SDK imports, workspace
and conversation classes, event shapes, HTTP paths or CLI flags. Everything else
in SceneWorks sees the generic `AgentBackend` protocol and the event vocabulary
in `app.events.types`.

Validated status
----------------
See docs/backends.md for the authoritative statement. In short, as validated by
WP2.5 against **openhands-sdk 1.17.0 + openhands-tools 1.17.0**:

- ``local`` mode (no Agent Server) executes real work and is validated for
  **read-only roles on Windows**;
- the OpenHands **terminal tool raises NotImplementedError on Windows**
  (upstream: ``openhands/tools/terminal/terminal/factory.py``), so any role
  needing shell — notably the Engineer — cannot run in local mode on Windows;
- ``remote``, ``http`` and ``cli`` modes are **implemented but not validated**:
  no Agent Server was available in the validation environment.

Gemini ACP remains the default and baseline backend.

Modes
-----
Resolved explicitly by `resolve_mode()`; there is no silent fallback, because a
backend that quietly degrades makes a failed run impossible to diagnose.

``local``   openhands-sdk + openhands-tools, LocalWorkspace, agent runs in this
            process. No server required. Shell unavailable on Windows.
``remote``  openhands-sdk against an Agent Server (``SCENEWORKS_OPENHANDS_URL``).
            NOTE the path caveat in `docs/limitations.md`: ``working_dir`` is a
            path in the *server's* filesystem, so a remote server does not see
            the local SceneWorks worktree.
``http``    REST polling, no SDK. Compatibility fallback.
``cli``     One-shot subprocess. Development fallback only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
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

logger = logging.getLogger("sceneworks.agents.openhands")

OPENHANDS_HEALTH_TIMEOUT = 10.0
OPENHANDS_RUN_TIMEOUT_FACTOR = 1.2
#: How long the event drainer waits for a queued event before re-checking
#: whether the worker thread has finished.
DRAIN_POLL_SECONDS = 0.05

MODE_LOCAL = "local"
MODE_REMOTE = "remote"
MODE_HTTP = "http"
MODE_CLI = "cli"
MODE_UNCONFIGURED = "unconfigured"

#: Modes proven to execute real work by WP2.5 live validation.
VALIDATED_MODES = frozenset({MODE_LOCAL})


class ModeResolution:
    """Which mode will be used, and why — reported rather than inferred."""

    def __init__(
        self,
        mode: str,
        detail: str,
        sdk: bool = False,
        tools: bool = False,
        shell: bool = False,
    ):
        self.mode = mode
        self.detail = detail
        self.sdk_available = sdk
        self.tools_available = tools
        self.shell_available = shell

    @property
    def validated(self) -> bool:
        return self.mode in VALIDATED_MODES

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "detail": self.detail,
            "sdk_available": self.sdk_available,
            "tools_available": self.tools_available,
            "shell_available": self.shell_available,
            "validated_mode": self.validated,
        }


#: Import probes are cached process-wide. Importing openhands.sdk pulls litellm
#: and opentelemetry and takes seconds; repeating it per health probe made the
#: dashboard slow, and doing it on the event loop stalled running workflows.
_MODULE_CACHE: dict[str, bool] = {}
_PROBE_MODULES = ("openhands.sdk", "openhands.tools.preset.default")


def _module_available(name: str) -> bool:
    """Whether a module can actually be imported. Cached.

    `importlib.util.find_spec` is not enough: `openhands.tools` installs fine but
    raises ModuleNotFoundError *during import* when its version does not match
    openhands-sdk. Only a real import proves usability, and WP2.5 hit exactly
    that mismatch (openhands-tools 1.42.1 against openhands-sdk 1.17.0).

    Callers on the event loop must warm the cache with `warm_module_cache()` in a
    worker thread first — a cold probe here blocks for seconds.
    """
    if name in _MODULE_CACHE:
        return _MODULE_CACHE[name]
    try:
        __import__(name)
        _MODULE_CACHE[name] = True
    except Exception:  # noqa: BLE001 - any import failure means unusable
        _MODULE_CACHE[name] = False
    return _MODULE_CACHE[name]


def warm_module_cache() -> None:
    """Populate the import-probe cache. Safe to call from a worker thread."""
    for name in _PROBE_MODULES:
        _module_available(name)


async def _warm_module_cache_async() -> None:
    """Warm the probe cache without blocking the event loop.

    Importing openhands.sdk inline froze the API for the duration of the import,
    which was long enough to stall an in-flight workflow to the point of
    cancellation.
    """
    if all(name in _MODULE_CACHE for name in _PROBE_MODULES):
        return
    await asyncio.to_thread(warm_module_cache)


def _shell_supported_locally() -> bool:
    """Whether OpenHands' terminal tool can run in this process.

    OpenHands V1 raises NotImplementedError for Windows in
    `openhands/tools/terminal/terminal/factory.py`. Detected by platform rather
    than by attempting a run, because the exception surfaces only once the agent
    is already executing.
    """
    return sys.platform != "win32"


class OpenHandsBackend(AgentBackend):
    key = "openhands"
    label = "OpenHands Agent SDK"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._conversations: dict[str, Any] = {}

    # ---------------------------------------------------------------- config

    def _server_url(self) -> str | None:
        return os.environ.get("SCENEWORKS_OPENHANDS_URL") or getattr(
            self._settings, "openhands_url", None
        )

    def _executable(self) -> str | None:
        exe = os.environ.get("SCENEWORKS_OPENHANDS_EXECUTABLE") or getattr(
            self._settings, "openhands_executable", None
        )
        return exe or shutil.which("openhands")

    def _model(self) -> str | None:
        return os.environ.get("SCENEWORKS_OPENHANDS_MODEL") or getattr(
            self._settings, "openhands_model", None
        )

    def _base_url(self) -> str | None:
        """LLM endpoint (any OpenAI-compatible server, e.g. LM Studio, vLLM).

        Distinct from `openhands_url`, which is the *Agent Server*. Without this,
        OpenHands could only be pointed at hosted providers, which made a local
        deterministic validation impossible.
        """
        return os.environ.get("SCENEWORKS_OPENHANDS_BASE_URL") or getattr(
            self._settings, "openhands_base_url", None
        )

    def _api_key(self) -> str:
        return os.environ.get("SCENEWORKS_OPENHANDS_API_KEY") or (
            getattr(self._settings, "openhands_api_key", None) or ""
        )

    def _session_key(self) -> str:
        return os.environ.get("OH_SESSION_API_KEYS_0") or ""

    def _configured_mode(self) -> str | None:
        value = os.environ.get("SCENEWORKS_OPENHANDS_MODE") or getattr(
            self._settings, "openhands_mode", None
        )
        return (value or "").strip().lower() or None

    def _http_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key := self._api_key():
            headers["Authorization"] = f"Bearer {api_key}"
        if session_key := self._session_key():
            headers["X-Session-API-Key"] = session_key
        return headers

    # ------------------------------------------------------------ mode logic

    def resolve_mode(self) -> ModeResolution:
        """Decide the execution mode explicitly.

        Order: an operator override wins; otherwise a configured Agent Server
        implies remote/http, and an importable SDK with no server implies local.
        """
        sdk = _module_available("openhands.sdk")
        tools = sdk and _module_available("openhands.tools.preset.default")
        shell = _shell_supported_locally()
        server_url = self._server_url()
        override = self._configured_mode()

        if override in (MODE_LOCAL, MODE_REMOTE, MODE_HTTP, MODE_CLI):
            return ModeResolution(
                override, f"mode forced by configuration: {override}", sdk, tools, shell
            )
        if override:
            return ModeResolution(
                MODE_UNCONFIGURED,
                f"unknown SCENEWORKS_OPENHANDS_MODE={override!r}; expected one of "
                f"{MODE_LOCAL}, {MODE_REMOTE}, {MODE_HTTP}, {MODE_CLI}",
                sdk, tools, shell,
            )

        if server_url:
            if sdk and tools:
                return ModeResolution(
                    MODE_REMOTE,
                    f"Agent Server configured ({server_url}) and openhands-sdk importable",
                    sdk, tools, shell,
                )
            return ModeResolution(
                MODE_HTTP,
                f"Agent Server configured ({server_url}) but the SDK is not usable; "
                "falling back to REST polling",
                sdk, tools, shell,
            )

        if sdk and tools:
            return ModeResolution(
                MODE_LOCAL,
                "openhands-sdk and openhands-tools importable; running in-process "
                "against a LocalWorkspace (no Agent Server required)",
                sdk, tools, shell,
            )

        if self._executable():
            return ModeResolution(
                MODE_CLI,
                "openhands executable found; CLI/headless development fallback",
                sdk, tools, shell,
            )

        missing = []
        if not sdk:
            missing.append("openhands-sdk not importable")
        elif not tools:
            missing.append(
                "openhands-tools not importable (install a version matching "
                "openhands-sdk; mismatched versions fail at import)"
            )
        return ModeResolution(
            MODE_UNCONFIGURED,
            "OpenHands not usable: "
            + "; ".join(missing or ["no SDK, executable or Agent Server URL"])
            + ". Install the 'openhands' extra, or set SCENEWORKS_OPENHANDS_URL "
            "or SCENEWORKS_OPENHANDS_EXECUTABLE.",
            sdk, tools, shell,
        )

    # ----------------------------------------------------------------- health

    async def health(self) -> BackendHealth:
        """Report whether OpenHands can actually execute work.

        Deliberately stricter than "configuration exists" or "HTTP 200": a
        backend reported healthy that then fails every execution is worse than
        one reported unavailable.
        """
        await _warm_module_cache_async()
        resolution = self.resolve_mode()

        if resolution.mode == MODE_UNCONFIGURED:
            return BackendHealth(
                key=self.key, label=self.label, available=False,
                detail=resolution.detail,
            )

        if resolution.mode in (MODE_LOCAL, MODE_REMOTE):
            return await self._health_sdk(resolution)
        if resolution.mode == MODE_HTTP:
            return await self._health_http(resolution)
        return await self._health_cli(resolution)

    async def _health_sdk(self, resolution: ModeResolution) -> BackendHealth:
        version = _sdk_version()
        notes: list[str] = [f"mode={resolution.mode}"]
        if version:
            notes.append(f"openhands-sdk {version}")

        # A model is mandatory: LLM() without one raises a pydantic
        # ValidationError deep inside the SDK, which surfaced as an opaque
        # "OpenHands SDK error" rather than a configuration problem.
        model = self._model()
        if not model:
            return BackendHealth(
                key=self.key, label=self.label, available=False, version=version,
                detail=(
                    "; ".join(notes)
                    + " — no model configured. Set SCENEWORKS_OPENHANDS_MODEL "
                    "(litellm form, e.g. 'lm_studio/<model>' or 'anthropic/<model>')."
                ),
            )
        notes.append(f"model={model}")

        if resolution.mode == MODE_LOCAL and not resolution.shell_available:
            notes.append(
                "shell UNAVAILABLE on this platform (OpenHands V1 terminal tool "
                "does not support Windows) — read-only roles only"
            )

        # Probe the LLM endpoint when one is configured. Reachability is not
        # proof the agent will succeed, but an unreachable endpoint is proof it
        # will not.
        base_url = self._base_url()
        if base_url:
            reachable, detail = await self._probe_llm_endpoint(base_url)
            if not reachable:
                return BackendHealth(
                    key=self.key, label=self.label, available=False, version=version,
                    detail="; ".join(notes) + f" — LLM endpoint unreachable: {detail}",
                )
            notes.append(f"llm endpoint OK ({base_url})")

        if resolution.mode == MODE_REMOTE:
            server_url = self._server_url() or ""
            ok, detail = await self._probe_server(server_url)
            if not ok:
                return BackendHealth(
                    key=self.key, label=self.label, available=False, version=version,
                    detail="; ".join(notes) + f" — Agent Server unreachable: {detail}",
                )
            notes.append(f"agent server OK ({server_url})")
            notes.append("mode NOT validated by SceneWorks qualification")

        return BackendHealth(
            key=self.key, label=self.label, available=True, version=version,
            detail="; ".join(notes),
        )

    async def _probe_llm_endpoint(self, base_url: str) -> tuple[bool, str]:
        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx is a hard dependency
            return False, "httpx not installed"
        url = base_url.rstrip("/") + "/models"
        try:
            async with httpx.AsyncClient(timeout=OPENHANDS_HEALTH_TIMEOUT) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                return True, "ok"
            return False, f"{url} returned {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"[:200]

    async def _probe_server(self, server_url: str) -> tuple[bool, str]:
        try:
            import httpx
        except ImportError:  # pragma: no cover
            return False, "httpx not installed"
        try:
            async with httpx.AsyncClient(timeout=OPENHANDS_HEALTH_TIMEOUT) as client:
                resp = await client.get(
                    f"{server_url.rstrip('/')}/health", headers=self._http_headers()
                )
            if resp.status_code == 200:
                return True, "ok"
            return False, f"/health returned {resp.status_code}: {resp.text[:120]}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"[:200]

    async def _health_http(self, resolution: ModeResolution) -> BackendHealth:
        ok, detail = await self._probe_server(self._server_url() or "")
        return BackendHealth(
            key=self.key, label=self.label, available=ok,
            detail=(
                f"mode=http (NOT validated); {resolution.detail}; "
                + ("agent server OK" if ok else f"agent server unreachable: {detail}")
            ),
        )

    async def _health_cli(self, resolution: ModeResolution) -> BackendHealth:
        executable = self._executable()
        if not executable:
            return BackendHealth(
                key=self.key, label=self.label, available=False,
                detail="CLI mode selected but no openhands executable found",
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                executable, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=OPENHANDS_HEALTH_TIMEOUT
            )
        except FileNotFoundError:
            return BackendHealth(
                key=self.key, label=self.label, available=False,
                detail=f"OpenHands executable not found: {executable}",
            )
        except (asyncio.TimeoutError, OSError) as exc:
            return BackendHealth(
                key=self.key, label=self.label, available=False,
                detail=f"Health check failed: {exc}",
            )
        if proc.returncode != 0:
            return BackendHealth(
                key=self.key, label=self.label, available=False,
                detail=(
                    f"`{executable} --version` failed ({proc.returncode}): "
                    + (stderr.decode(errors="replace").strip() or "unknown error")
                )[:300],
            )
        lines = stdout.decode(errors="replace").strip().splitlines()
        return BackendHealth(
            key=self.key, label=self.label, available=True,
            version=lines[0] if lines else "unknown",
            detail=(
                f"mode=cli (NOT validated — development fallback); "
                f"executable: {executable}"
            ),
        )

    # -------------------------------------------------------------- lifecycle

    async def cancel(self, execution_id: str) -> None:
        """Stop a running conversation.

        `pause()` is the SDK's cooperative stop; it is what actually interrupts
        `run()`. The previous implementation only set an asyncio Event and called
        `close()`, neither of which could take effect because `run()` was
        blocking the event loop — cancellation was decorative.
        """
        if event := self._cancel_events.get(execution_id):
            event.set()
        conversation = self._conversations.get(execution_id)
        if conversation is None:
            return
        for method in ("pause", "close"):
            fn = getattr(conversation, method, None)
            if fn is None:
                continue
            try:
                # Called from the event loop while the worker thread runs the
                # conversation; both are documented as safe to call externally.
                await asyncio.to_thread(fn)
            except Exception:  # noqa: BLE001 - cancellation must never raise
                logger.debug("openhands %s() failed during cancel", method, exc_info=True)

    async def run(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
    ) -> AgentResult:
        await _warm_module_cache_async()
        resolution = self.resolve_mode()

        await event_sink.emit(
            "agent.event",
            {
                "name": "backend.mode",
                "message": f"OpenHands mode: {resolution.mode} — {resolution.detail}",
                "diagnostics": True,
                **resolution.as_dict(),
            },
        )

        if resolution.mode == MODE_UNCONFIGURED:
            return AgentResult(status="failed", error=resolution.detail)

        if resolution.mode in (MODE_LOCAL, MODE_REMOTE):
            return await self._run_sdk(request, workspace, event_sink, resolution)
        if resolution.mode == MODE_HTTP:
            return await self._run_http(
                request, workspace, event_sink, self._server_url() or ""
            )
        return await self._run_cli(
            request, workspace, event_sink, self._executable() or "openhands"
        )

    # ------------------------------------------------------------- SDK mode

    async def _run_sdk(
        self,
        request: AgentRequest,
        workspace: Workspace,
        event_sink: AgentEventSink,
        resolution: ModeResolution,
    ) -> AgentResult:
        model = self._model()
        if not model:
            return AgentResult(
                status="failed",
                error=(
                    "OpenHands requires a model: set SCENEWORKS_OPENHANDS_MODEL "
                    "(litellm form, e.g. 'lm_studio/<model>')."
                ),
            )

        needs_shell = "shell_execute" in (workspace.permissions or ())
        if (
            resolution.mode == MODE_LOCAL
            and needs_shell
            and not resolution.shell_available
        ):
            # Fail with the real reason instead of letting the terminal tool
            # raise NotImplementedError once the agent is already running.
            return AgentResult(
                status="failed",
                error=(
                    "This role requires shell access, but the OpenHands V1 terminal "
                    "tool does not support Windows in local mode. Use Gemini ACP for "
                    "roles needing shell, or run an OpenHands Agent Server on Linux "
                    "and set SCENEWORKS_OPENHANDS_URL."
                ),
            )

        cancel_event = asyncio.Event()
        self._cancel_events[request.execution_id] = cancel_event
        events: queue.Queue = queue.Queue()
        collected: list[Any] = []
        done = threading.Event()

        try:
            from openhands.sdk import LLM, Agent, Conversation, Workspace as OHWorkspace
            from openhands.tools.preset.default import get_default_agent
        except Exception as exc:  # noqa: BLE001
            # Explicit failure, not a silent fallback: a degraded mode that
            # nobody was told about is undiagnosable.
            return AgentResult(
                status="failed",
                error=(
                    f"OpenHands SDK import failed ({type(exc).__name__}: {exc}). "
                    "openhands-sdk and openhands-tools versions must match."
                )[:2000],
            )

        try:
            llm_kwargs: dict[str, Any] = {"model": model, "service_id": "sceneworks"}
            if base_url := self._base_url():
                llm_kwargs["base_url"] = base_url
            if api_key := self._api_key():
                llm_kwargs["api_key"] = api_key
            elif base_url:
                # Local OpenAI-compatible servers reject an absent key but accept
                # any value.
                llm_kwargs["api_key"] = "sceneworks-local"

            llm = LLM(**llm_kwargs)
            base_agent = get_default_agent(llm=llm, cli_mode=True)

            tools = list(getattr(base_agent, "tools", []) or [])
            if not resolution.shell_available:
                kept = [t for t in tools if not _is_terminal_tool(t)]
                if len(kept) != len(tools):
                    await event_sink.emit(
                        "agent.event",
                        {
                            "name": "backend.tools_restricted",
                            "message": (
                                "terminal tool removed: OpenHands V1 does not support "
                                "shell on this platform. The agent can read and edit "
                                "files but cannot run commands."
                            ),
                            "diagnostics": True,
                        },
                    )
                agent = Agent(llm=llm, tools=kept, include_default_tools=[])
            else:
                agent = base_agent

            if resolution.mode == MODE_REMOTE:
                oh_workspace = OHWorkspace(
                    host=(self._server_url() or "").rstrip("/"),
                    api_key=self._session_key() or None,
                    working_dir=str(workspace.path),
                )
            else:
                oh_workspace = OHWorkspace(working_dir=str(workspace.path))

            prompt = (
                f"# Role instructions\n{request.system_prompt}\n\n"
                f"# Request\n{request.user_prompt}"
            )

            conversation = Conversation(
                agent,
                workspace=oh_workspace,
                callbacks=[events.put],
                # The SDK's console visualizer writes emoji, which raises
                # UnicodeEncodeError on a cp1252 Windows console and killed the
                # run before any work happened.
                visualizer=None,
                # The SDK default is 500 turns. A model that never concludes
                # consumes all of them and the execution runs to the hard
                # timeout with nothing to show; a bound turns that into a
                # finished run with partial output.
                max_iteration_per_run=max(
                    1, int(getattr(self._settings, "openhands_max_iterations", 40))
                ),
            )
            self._conversations[request.execution_id] = conversation

            def worker() -> dict:
                try:
                    conversation.send_message(prompt)
                    conversation.run()
                    return {"ok": True}
                except Exception as exc:  # noqa: BLE001 - reported to the caller
                    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                finally:
                    done.set()

            # The SDK is synchronous. Running it inline blocked the API event
            # loop for the whole execution, freezing SSE and every HTTP request.
            worker_task = asyncio.create_task(asyncio.to_thread(worker))
            drainer = asyncio.create_task(
                self._drain_events(events, collected, event_sink, done)
            )

            timeout = self._settings.execution_timeout_seconds * OPENHANDS_RUN_TIMEOUT_FACTOR
            try:
                outcome = await asyncio.wait_for(worker_task, timeout=timeout)
            except asyncio.TimeoutError:
                await self.cancel(request.execution_id)
                done.set()
                await drainer
                return AgentResult(
                    status="failed",
                    error=f"OpenHands execution exceeded {timeout:.0f}s",
                )
            finally:
                done.set()
                with_suppressed = getattr(drainer, "cancel", None)
                if with_suppressed and not drainer.done():
                    await drainer

            if event_sink.cancelled() or cancel_event.is_set():
                return AgentResult(status="cancelled", error="cancelled by user")

            if not outcome.get("ok"):
                return AgentResult(status="failed", error=outcome.get("error", "")[:2000])

            summary = _summarize(collected)
            return AgentResult(status="completed", summary=summary)

        except Exception as exc:  # noqa: BLE001 - adapter boundary
            logger.exception("openhands SDK run failed")
            return AgentResult(
                status="failed", error=f"OpenHands SDK error: {exc}"[:2000],
            )
        finally:
            conversation = self._conversations.pop(request.execution_id, None)
            if conversation is not None:
                close = getattr(conversation, "close", None)
                if close is not None:
                    try:
                        await asyncio.to_thread(close)
                    except Exception:  # noqa: BLE001
                        logger.debug("openhands close() failed", exc_info=True)
            self._cancel_events.pop(request.execution_id, None)

    async def _drain_events(
        self,
        events: queue.Queue,
        collected: list[Any],
        event_sink: AgentEventSink,
        done: threading.Event,
    ) -> None:
        """Forward SDK callback events to SceneWorks while the worker runs.

        Callbacks fire on the worker thread, so they are handed over through a
        thread-safe queue and translated here, on the event loop. This is what
        makes OpenHands output stream during a run; reading
        `conversation.events` after `run()` returned produced nothing at all —
        LocalConversation has no `events` attribute, and the previous code's
        `getattr(conversation, "events", [])` silently yielded an empty list.
        """
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                if done.is_set() and events.empty():
                    return
                await asyncio.sleep(DRAIN_POLL_SECONDS)
                continue
            collected.append(event)
            try:
                await self._emit_mapped(event, event_sink)
            except Exception:  # noqa: BLE001 - a mapping bug must not fail the run
                logger.debug("openhands event mapping failed", exc_info=True)

    async def _emit_mapped(self, event: Any, event_sink: AgentEventSink) -> None:
        """Map one OpenHands event onto the generic SceneWorks vocabulary.

        Mapped by class name because the SDK's event classes are pydantic models
        whose module paths have moved between versions; the names have been
        stable. Unrecognised events become a diagnostic `agent.event` rather than
        being dropped, so a new SDK event type is visible instead of silent.

        Not produced: `test.result` (OpenHands reports no structured test
        outcome) and `git.commit` (the agent commits through the shell;
        SceneWorks captures the commit itself). Documented in docs/backends.md.
        """
        name = type(event).__name__

        if name == "MessageEvent":
            if text := _message_text(event):
                await event_sink.emit("agent.message", {"text": text[:4000]})
            return

        if name == "ActionEvent":
            tool = getattr(event, "tool_name", None) or "tool"
            if thought := _as_text(getattr(event, "thought", None)):
                await event_sink.emit(
                    "agent.thought_summary", {"text": thought[:2000]}
                )
            payload = {
                "tool": tool,
                "tool_call_id": getattr(event, "tool_call_id", None),
                "security_risk": str(getattr(event, "security_risk", "") or ""),
            }
            await event_sink.emit("tool.started", payload)
            if _is_terminal_name(tool):
                command = _action_field(event, ("command", "cmd"))
                await event_sink.emit(
                    "command.started", {"command": (command or "")[:2000], "tool": tool}
                )
            elif _is_editor_name(tool):
                path = _action_field(event, ("path", "file_path"))
                if path:
                    await event_sink.emit("file.changed", {"path": str(path)})
            return

        if name in ("ObservationEvent", "UserRejectObservation"):
            tool = getattr(event, "tool_name", None) or "tool"
            text = _as_text(getattr(event, "observation", None))
            await event_sink.emit(
                "tool.completed",
                {"tool": tool, "tool_call_id": getattr(event, "tool_call_id", None)},
            )
            if _is_terminal_name(tool) and text:
                await event_sink.emit("command.output", {"chunk": text[:4000]})
            return

        if name == "AgentErrorEvent":
            await event_sink.emit(
                "agent.event",
                {
                    "name": "openhands.agent_error",
                    "message": _as_text(getattr(event, "error", None))
                    or _as_text(getattr(event, "message", None))
                    or "agent error",
                },
                severity="error",
            )
            return

        if name == "PauseEvent":
            await event_sink.emit(
                "agent.event",
                {"name": "openhands.paused", "message": "conversation paused (cancellation)"},
                severity="warning",
            )
            return

        # SystemPromptEvent, Condensation*, TokenEvent, LLMCompletionLogEvent, ...
        await event_sink.emit(
            "agent.event",
            {"name": f"openhands.{name}", "diagnostics": True},
        )

    # ------------------------------------------------------- HTTP mode
    # Implemented but NOT validated: no Agent Server was available in the
    # WP2.5 validation environment. Paths reflect the documented REST surface.

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
                create_resp = await client.post(
                    f"{server_url.rstrip('/')}/api/conversations",
                    headers=self._http_headers(),
                    json={"directory": str(workspace.path), "model": self._model()},
                )
                if create_resp.status_code not in (200, 201):
                    return AgentResult(
                        status="failed",
                        error=f"Failed to create conversation: {create_resp.status_code}",
                    )
                conversation = create_resp.json()
                conversation_id = conversation.get("id") or conversation.get("conversation_id")

                prompt = (
                    f"# Role instructions\n{request.system_prompt}\n\n"
                    f"# Request\n{request.user_prompt}"
                )
                msg_resp = await client.post(
                    f"{server_url.rstrip('/')}/api/conversations/{conversation_id}/messages",
                    headers=self._http_headers(),
                    json={"content": prompt},
                )
                if msg_resp.status_code not in (200, 201):
                    return AgentResult(
                        status="failed",
                        error=f"Failed to send message: {msg_resp.status_code}",
                    )

                summary_parts: list[str] = []
                seen: set[int] = set()
                while True:
                    if event_sink.cancelled() or cancel_event.is_set():
                        try:
                            await client.delete(
                                f"{server_url.rstrip('/')}/api/conversations/{conversation_id}",
                                headers=self._http_headers(),
                            )
                        except Exception:  # noqa: BLE001
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

                    for idx, msg in enumerate(conv.get("messages") or conv.get("events") or []):
                        if idx in seen:
                            continue
                        seen.add(idx)
                        content = msg.get("content") or msg.get("message") or ""
                        if isinstance(content, str) and content.strip():
                            summary_parts.append(content)
                            if len(summary_parts) <= 20:
                                await event_sink.emit(
                                    "agent.message", {"text": content[:4000]}
                                )

                    if state in ("completed", "finished", "done", "success"):
                        return AgentResult(
                            status="completed",
                            summary="\n".join(summary_parts[-5:]) or "OpenHands completed.",
                        )
                    if state in ("failed", "error"):
                        detail = conv.get("error") or conv.get("detail") or "Unknown error"
                        return AgentResult(status="failed", error=str(detail)[:2000])
                    if state in ("stopped", "terminated", "cancelled"):
                        return AgentResult(status="cancelled", error="Cancelled by server")

                    await asyncio.sleep(1.0)
        finally:
            self._cancel_events.pop(request.execution_id, None)

    # ------------------------------------------------------- CLI mode
    # Development fallback only; NOT validated. Flags are version-dependent.

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
        if model := self._model():
            env["LLM_MODEL"] = model
        for key, val in (self._settings.openhands_environment or {}).items():
            env[key] = val

        prompt = (
            f"# Role instructions\n{request.system_prompt}\n\n"
            f"# Request\n{request.user_prompt}"
        )
        cmd = [executable, "run", "--task", prompt[:4000], "--directory", str(workspace.path)]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(workspace.path), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                ),
            )
        except FileNotFoundError:
            return AgentResult(
                status="failed", error=f"OpenHands executable not found: {executable}",
            )

        try:
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []

            async def read_stream(stream, chunks, label):
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
                                await event_sink.emit(label, {"chunk": text[:2000]})
                except (asyncio.CancelledError, ValueError):
                    pass

            stdout_task = asyncio.create_task(
                read_stream(proc.stdout, stdout_chunks, "command.output")
            )
            stderr_task = asyncio.create_task(
                read_stream(proc.stderr, stderr_chunks, "command.output")
            )
            try:
                rc = await asyncio.wait_for(
                    proc.wait(), timeout=self._settings.execution_timeout_seconds + 60
                )
            except asyncio.TimeoutError:
                await self._kill_proc(proc)
                return AgentResult(status="failed", error="OpenHands execution timed out")

            await stdout_task
            await stderr_task

            if event_sink.cancelled() or cancel_event.is_set():
                return AgentResult(status="cancelled", error="cancelled by user")
            if rc != 0:
                return AgentResult(
                    status="failed",
                    error=("\n".join(stderr_chunks[-10:]) or f"Exit code {rc}")[:2000],
                )
            return AgentResult(
                status="completed",
                summary="\n".join(stdout_chunks[-10:]).strip() or "OpenHands completed.",
            )
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


# ------------------------------------------------------------------ helpers


def _sdk_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("openhands-sdk")
    except Exception:  # noqa: BLE001
        return None


def _is_terminal_tool(tool: Any) -> bool:
    return _is_terminal_name(str(getattr(tool, "name", "") or tool))


def _is_terminal_name(name: str) -> bool:
    lowered = str(name).lower()
    return any(token in lowered for token in ("terminal", "bash", "execute_bash", "shell"))


def _is_editor_name(name: str) -> bool:
    lowered = str(name).lower()
    return "editor" in lowered or "file" in lowered


def _action_field(event: Any, names: tuple[str, ...]) -> str | None:
    """Read a field off an ActionEvent's `action` payload.

    The action is a pydantic model whose field names differ per tool and have
    moved between SDK versions, so several candidates are tried and a miss is
    reported as None rather than guessed at.
    """
    action = getattr(event, "action", None)
    for source in (action, event):
        if source is None:
            continue
        for name in names:
            value = getattr(source, name, None)
            if isinstance(value, str) and value.strip():
                return value
        # Some versions expose the payload as a dict.
        if isinstance(source, dict):
            for name in names:
                value = source.get(name)
                if isinstance(value, str) and value.strip():
                    return value
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for attr in ("text", "content", "message", "output", "result"):
        inner = getattr(value, attr, None)
        if isinstance(inner, str) and inner.strip():
            return inner
    if isinstance(value, (list, tuple)):
        return "\n".join(_as_text(v) for v in value if _as_text(v))
    return str(value)[:4000]


def _message_text(event: Any) -> str:
    """Pull assistant text out of a MessageEvent.

    The payload lives on `llm_message`, whose `content` is a list of content
    blocks in recent SDK versions and a plain string in older ones.
    """
    message = getattr(event, "llm_message", None)
    if message is None:
        return _as_text(event)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return _as_text(message)


def _summarize(collected: list[Any]) -> str:
    """Build the execution summary from the assistant's own messages."""
    texts: list[str] = []
    for event in collected:
        if type(event).__name__ != "MessageEvent":
            continue
        source = str(getattr(event, "source", "") or "").lower()
        if source and source not in ("agent", "assistant"):
            continue
        if text := _message_text(event):
            texts.append(text.strip())
    if texts:
        return "\n\n".join(texts[-3:])[:20_000]
    return "OpenHands completed without producing a textual summary."
