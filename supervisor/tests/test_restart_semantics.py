from __future__ import annotations

import unittest

from supervisor.core import LifecycleSupervisor
from supervisor.http_api import SupervisorApplication
from supervisor.journal import OperationJournal
from supervisor.model import ComponentKey
from supervisor.providers import FakeHealthProvider, FakeProcessProvider


class RestartSemanticsTests(unittest.TestCase):
    def test_restart_all_requires_old_endpoints_down_before_new_processes_start(self) -> None:
        processes = FakeProcessProvider()
        health = FakeHealthProvider()
        for component in ComponentKey:
            processes.set_process(component, running=True, owned=True)
            health.set_healthy(component, True)
        supervisor = LifecycleSupervisor(process_provider=processes, health_provider=health)
        supervisor.reconcile()
        processes.calls.clear()

        for component in ComponentKey:
            health.queue_results(component, [False, True])
        supervisor.restart_all(actor="local_cli")

        self.assertEqual(
            processes.calls,
            [
                ("stop", ComponentKey.MCP_TUNNEL),
                ("stop", ComponentKey.WEB),
                ("stop", ComponentKey.API),
                ("start", ComponentKey.API),
                ("start", ComponentKey.WEB),
                ("start", ComponentKey.MCP_TUNNEL),
            ],
        )


if __name__ == "__main__":
    unittest.main()
