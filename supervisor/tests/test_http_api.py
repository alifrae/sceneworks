from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

from supervisor.core import LifecycleSupervisor
from supervisor.http_api import SupervisorApplication, create_server
from supervisor.journal import OperationJournal
from supervisor.model import ComponentKey
from supervisor.providers import FakeHealthProvider, FakeProcessProvider


class SupervisorHttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.processes = FakeProcessProvider()
        self.health = FakeHealthProvider()
        for component in ComponentKey:
            self.processes.set_process(component, running=True, owned=True)
            self.health.set_healthy(component, True)
        supervisor = LifecycleSupervisor(
            process_provider=self.processes,
            health_provider=self.health,
        )
        supervisor.reconcile()
        self.processes.calls.clear()
        journal = OperationJournal(Path(self.tmp.name) / "supervisor.db")
        self.app = SupervisorApplication(
            supervisor=supervisor,
            journal=journal,
            token="unit-test-token",
            start_worker=False,
        )
        self.server = create_server(self.app, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _json_request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: str | None = None,
    ) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(
            self.base_url + path,
            method=method,
            data=data,
            headers=headers,
        )
        try:
            with request.urlopen(req, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_mutation_requires_bearer_token(self) -> None:
        status, body = self._json_request(
            "POST",
            "/v1/actions/restart",
            {"component": "api"},
        )

        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")
        self.assertEqual(self.app.journal.list(limit=10), [])
        self.assertEqual(self.processes.calls, [])

    def test_restart_all_is_durably_accepted_before_execution(self) -> None:
        status, body = self._json_request(
            "POST",
            "/v1/actions/restart-all",
            token="unit-test-token",
        )

        self.assertEqual(status, 202)
        operation_id = body["operation_id"]
        row = self.app.journal.get(operation_id)
        assert row is not None
        self.assertEqual(row["state"], "ACCEPTED")
        self.assertEqual(row["action"], "restart_all")
        self.assertEqual(self.processes.calls, [])

        self.assertTrue(self.app.process_next())
        row = self.app.journal.get(operation_id)
        assert row is not None
        self.assertEqual(row["state"], "SUCCEEDED")
        self.assertEqual(
            self.processes.calls,
            [
                ("stop", ComponentKey.MCP_TUNNEL),
                ("stop", ComponentKey.WEB),
                ("stop", ComponentKey.API),
                ("start", ComponentKey.API),
                ("start", ComponentKey.WEB),
                ("start", ComponentKey.MCP_TUNNEL),
            ],
        )

    def test_restart_rejects_unknown_component_without_operation(self) -> None:
        status, body = self._json_request(
            "POST",
            "/v1/actions/restart",
            {"component": "database"},
            token="unit-test-token",
        )

        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_component")
        self.assertEqual(self.app.journal.list(limit=10), [])
        self.assertEqual(self.processes.calls, [])

    def test_status_and_operation_payloads_do_not_expose_token(self) -> None:
        status, body = self._json_request("GET", "/v1/status")
        self.assertEqual(status, 200)
        serialized = json.dumps(body)
        self.assertNotIn("unit-test-token", serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertEqual(body["aggregate_state"], "HEALTHY")
        self.assertEqual(set(body["components"]), {"api", "web", "mcp_tunnel"})


if __name__ == "__main__":
    unittest.main()
