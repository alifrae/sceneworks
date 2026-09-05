from __future__ import annotations

import hmac
import json
import queue
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from supervisor.core import LifecycleError, LifecycleSupervisor
from supervisor.journal import OperationJournal
from supervisor.model import ComponentKey, ComponentState, SupervisorStatus

_ALLOWED_ACTORS = {"user_ui", "launcher", "local_cli", "mcp"}
_ALLOWED_ACTIONS = {"start", "stop", "restart", "reconcile", "restart_all"}


class SupervisorApplication:
    def __init__(
        self,
        *,
        supervisor: LifecycleSupervisor,
        journal: OperationJournal,
        token: str,
        start_worker: bool = True,
        monitor_interval: float = 5.0,
    ) -> None:
        self.supervisor = supervisor
        self.journal = journal
        self.token = token
        self.monitor_interval = max(float(monitor_interval), 0.1)
        self._queue: queue.Queue[tuple[str, str, ComponentKey | None, str]] = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.supervisor.set_auto_recovery_sink(self._journaled_auto_recovery)
        if start_worker:
            self._threads = [
                threading.Thread(target=self._worker_loop, name="sceneworks-supervisor-worker", daemon=True),
                threading.Thread(target=self._monitor_loop, name="sceneworks-supervisor-monitor", daemon=True),
            ]
            for thread in self._threads:
                thread.start()

    def submit(
        self,
        *,
        action: str,
        component: ComponentKey | None,
        actor: str,
    ) -> str:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"invalid lifecycle action: {action}")
        if actor not in _ALLOWED_ACTORS:
            raise ValueError(f"invalid lifecycle actor: {actor}")
        if action in {"start", "stop", "restart"} and component is None:
            raise ValueError(f"component required for {action}")
        if action in {"reconcile", "restart_all"} and component is not None:
            raise ValueError(f"component not allowed for {action}")
        operation_id = self.journal.accept(
            actor=actor,
            action=action,
            component=None if component is None else component.value,
        )
        self._queue.put((operation_id, action, component, actor))
        return operation_id

    def process_next(self) -> bool:
        try:
            operation_id, action, component, actor = self._queue.get_nowait()
        except queue.Empty:
            return False
        try:
            self.journal.mark_running(operation_id)
            self._dispatch(action=action, component=component, actor=actor)
        except LifecycleError as exc:
            self.journal.finish(operation_id, result="FAILED", detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - lifecycle boundary must preserve audit state
            self.journal.finish(
                operation_id,
                result="FAILED",
                detail=f"{type(exc).__name__}: {exc}",
            )
        else:
            self.journal.finish(operation_id, result="SUCCEEDED")
        finally:
            self._queue.task_done()
        return True

    def monitor_once(self) -> SupervisorStatus:
        return self.supervisor.monitor_once()

    def shutdown(self) -> None:
        self._stop.set()
        self.supervisor.set_auto_recovery_sink(None)
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=1.0)

    def _dispatch(
        self,
        *,
        action: str,
        component: ComponentKey | None,
        actor: str,
    ) -> None:
        if action == "restart_all":
            self.supervisor.restart_all(actor=actor)
            return
        if action == "reconcile":
            self.supervisor.reconcile()
            return
        assert component is not None
        if action == "start":
            self.supervisor.start(component, actor=actor)
        elif action == "stop":
            self.supervisor.stop(component, actor=actor)
        elif action == "restart":
            self.supervisor.restart(component, actor=actor)
        else:  # pragma: no cover - submit validates actions
            raise ValueError(action)

    def _journaled_auto_recovery(
        self,
        component: ComponentKey,
        recover: callable,
    ) -> None:
        operation_id = self.journal.accept(
            actor="auto",
            action="restart",
            component=component.value,
        )
        self.journal.mark_running(operation_id)
        try:
            recover()
            state = self.supervisor.status().components[component].state
            if state == ComponentState.HEALTHY:
                self.journal.finish(operation_id, result="SUCCEEDED")
            else:
                self.journal.finish(
                    operation_id,
                    result="FAILED",
                    detail=f"automatic recovery ended in {state.value}",
                )
        except Exception as exc:  # noqa: BLE001 - audit recovery failure
            self.journal.finish(
                operation_id,
                result="FAILED",
                detail=f"{type(exc).__name__}: {exc}",
            )

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if not self.process_next():
                self._stop.wait(0.2)

    def _monitor_loop(self) -> None:
        while not self._stop.wait(self.monitor_interval):
            try:
                self.monitor_once()
            except Exception:
                # The next monitor cycle must still run. Operational failures are
                # captured by lifecycle operations rather than crashing supervision.
                continue


def serialize_status(status: SupervisorStatus) -> dict[str, Any]:
    return {
        "aggregate_state": status.aggregate_state.value,
        "components": {
            component.value: {
                "state": row.state.value,
                "consecutive_failures": row.consecutive_failures,
                "restart_attempts": row.restart_attempts,
                "last_transition_at": row.last_transition_at,
                "healthy_since": row.healthy_since,
            }
            for component, row in status.components.items()
        },
    }


def create_server(
    app: SupervisorApplication,
    *,
    host: str = "127.0.0.1",
    port: int = 8020,
) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("WP21 supervisor must bind to 127.0.0.1")

    class Handler(_SupervisorHandler):
        application = app

    return ThreadingHTTPServer((host, port), Handler)


class _SupervisorHandler(BaseHTTPRequestHandler):
    application: SupervisorApplication
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/status":
            self._send_json(HTTPStatus.OK, serialize_status(self.application.supervisor.status()))
            return
        if parsed.path == "/v1/operations":
            query = parse_qs(parsed.query)
            try:
                limit = min(max(int(query.get("limit", ["20"])[0]), 1), 100)
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_limit"})
                return
            self._send_json(HTTPStatus.OK, {"operations": self.application.journal.list(limit=limit)})
            return
        prefix = "/v1/operations/"
        if parsed.path.startswith(prefix):
            operation_id = parsed.path[len(prefix) :]
            if not operation_id or "/" in operation_id:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            row = self.application.journal.get(operation_id)
            if row is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "operation_not_found"})
                return
            self._send_json(HTTPStatus.OK, row)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        parsed = urlparse(self.path)
        prefix = "/v1/actions/"
        if not parsed.path.startswith(prefix):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        action_path = parsed.path[len(prefix) :]
        action = "restart_all" if action_path == "restart-all" else action_path
        if action not in _ALLOWED_ACTIONS:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown_action"})
            return

        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        actor = self.headers.get("X-SceneWorks-Actor", "user_ui").strip()
        if actor not in _ALLOWED_ACTORS:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_actor"})
            return

        component: ComponentKey | None = None
        if action in {"start", "stop", "restart"}:
            raw_component = body.get("component")
            try:
                component = ComponentKey(str(raw_component))
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_component"})
                return
        elif body:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "unexpected_body"})
            return

        try:
            operation_id = self.application.submit(
                action=action,
                component=component,
                actor=actor,
            )
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"operation_id": operation_id})

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        supplied = header[len(prefix) :]
        return bool(supplied) and hmac.compare_digest(supplied, self.application.token)

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid_content_length") from exc
        if length < 0 or length > 16384:
            raise ValueError("request_too_large")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_json") from exc
        if not isinstance(value, dict):
            raise ValueError("json_object_required")
        return value

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


__all__ = ["SupervisorApplication", "create_server", "serialize_status"]
