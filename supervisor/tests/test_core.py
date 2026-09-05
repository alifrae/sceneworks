from __future__ import annotations

import unittest

from supervisor.core import LifecycleError, LifecycleSupervisor
from supervisor.model import ComponentKey, ComponentState
from supervisor.providers import FakeHealthProvider, FakeProcessProvider


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class LifecycleSupervisorCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.processes = FakeProcessProvider()
        self.health = FakeHealthProvider()
        for component in ComponentKey:
            self.processes.set_process(component, running=True, owned=True)
            self.health.set_healthy(component, True)
        self.supervisor = LifecycleSupervisor(
            process_provider=self.processes,
            health_provider=self.health,
            clock=self.clock.time,
            sleep=self.clock.sleep,
        )
        self.supervisor.reconcile()
        self.processes.calls.clear()

    def test_three_failed_samples_trigger_automatic_restart(self) -> None:
        self.health.set_healthy(ComponentKey.API, False)

        self.supervisor.monitor_once()
        self.supervisor.monitor_once()
        self.assertEqual(self.processes.calls, [])

        self.health.queue_results(ComponentKey.API, [False, True])
        self.supervisor.monitor_once()

        self.assertEqual(
            self.processes.calls,
            [("stop", ComponentKey.API), ("start", ComponentKey.API)],
        )
        self.assertEqual(self.clock.sleeps, [1.0])
        self.assertEqual(
            self.supervisor.status().components[ComponentKey.API].state,
            ComponentState.HEALTHY,
        )

    def test_owned_process_exit_triggers_immediate_recovery(self) -> None:
        self.processes.set_process(ComponentKey.API, running=False, owned=True)
        self.health.set_healthy(ComponentKey.API, False)
        self.health.queue_results(ComponentKey.API, [True])

        self.supervisor.monitor_once()

        self.assertEqual(self.processes.calls, [("start", ComponentKey.API)])
        self.assertEqual(self.clock.sleeps, [1.0])
        self.assertEqual(
            self.supervisor.status().components[ComponentKey.API].state,
            ComponentState.HEALTHY,
        )

    def test_restart_budget_exhaustion_transitions_to_degraded(self) -> None:
        self.health.set_healthy(ComponentKey.API, False)

        self.supervisor.monitor_once()
        self.supervisor.monitor_once()
        self.supervisor.monitor_once()

        starts = [call for call in self.processes.calls if call == ("start", ComponentKey.API)]
        self.assertEqual(len(starts), 3)
        self.assertEqual(self.clock.sleeps, [1.0, 2.0, 5.0])
        api = self.supervisor.status().components[ComponentKey.API]
        self.assertEqual(api.state, ComponentState.DEGRADED)
        self.assertEqual(api.restart_attempts, 3)

    def test_ten_healthy_minutes_clear_restart_budget(self) -> None:
        self.health.set_healthy(ComponentKey.API, False)
        self.supervisor.monitor_once()
        self.supervisor.monitor_once()
        self.health.queue_results(ComponentKey.API, [False, True])
        self.supervisor.monitor_once()
        self.assertEqual(
            self.supervisor.status().components[ComponentKey.API].restart_attempts,
            1,
        )

        self.health.set_healthy(ComponentKey.API, True)
        self.supervisor.monitor_once()
        self.clock.advance(600.0)
        self.supervisor.monitor_once()

        self.assertEqual(
            self.supervisor.status().components[ComponentKey.API].restart_attempts,
            0,
        )

    def test_restart_all_uses_dependency_order(self) -> None:
        self.supervisor.restart_all(actor="local_cli")

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

    def test_ambiguous_ownership_is_never_stopped(self) -> None:
        self.processes.set_process(ComponentKey.API, running=True, owned=False)
        self.supervisor.reconcile()
        self.processes.calls.clear()

        with self.assertRaises(LifecycleError):
            self.supervisor.stop(ComponentKey.API, actor="local_cli")

        self.assertEqual(self.processes.calls, [])
        self.assertIn(
            self.supervisor.status().components[ComponentKey.API].state,
            {ComponentState.UNKNOWN, ComponentState.DEGRADED},
        )


if __name__ == "__main__":
    unittest.main()
