from __future__ import annotations

import unittest

from supervisor.core import LifecycleError, LifecycleSupervisor
from supervisor.model import ComponentKey, ComponentSpec, ComponentState
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


class StartupPolicyTests(unittest.TestCase):
    def test_start_waits_within_component_grace_period(self) -> None:
        clock = FakeClock()
        processes = FakeProcessProvider()
        health = FakeHealthProvider()
        processes.set_process(ComponentKey.API, running=False, owned=False)
        health.set_healthy(ComponentKey.API, False)
        health.queue_results(ComponentKey.API, [False, False, True])
        supervisor = LifecycleSupervisor(
            process_provider=processes,
            health_provider=health,
            clock=clock.time,
            sleep=clock.sleep,
            component_specs={
                ComponentKey.API: ComponentSpec(ComponentKey.API, 1.0),
                ComponentKey.WEB: ComponentSpec(ComponentKey.WEB, 1.0),
                ComponentKey.MCP_TUNNEL: ComponentSpec(ComponentKey.MCP_TUNNEL, 1.0),
            },
            startup_poll_seconds=0.25,
            enabled_components={ComponentKey.API},
        )

        supervisor.start(ComponentKey.API, actor="local_cli")

        self.assertEqual(processes.calls, [("start", ComponentKey.API)])
        self.assertEqual(clock.sleeps, [0.25])
        self.assertEqual(
            supervisor.status().components[ComponentKey.API].state,
            ComponentState.HEALTHY,
        )

    def test_start_fails_after_grace_period_expires(self) -> None:
        clock = FakeClock()
        processes = FakeProcessProvider()
        health = FakeHealthProvider()
        processes.set_process(ComponentKey.API, running=False, owned=False)
        health.set_healthy(ComponentKey.API, False)
        supervisor = LifecycleSupervisor(
            process_provider=processes,
            health_provider=health,
            clock=clock.time,
            sleep=clock.sleep,
            component_specs={
                ComponentKey.API: ComponentSpec(ComponentKey.API, 0.5),
                ComponentKey.WEB: ComponentSpec(ComponentKey.WEB, 1.0),
                ComponentKey.MCP_TUNNEL: ComponentSpec(ComponentKey.MCP_TUNNEL, 1.0),
            },
            startup_poll_seconds=0.25,
            enabled_components={ComponentKey.API},
        )

        with self.assertRaises(LifecycleError):
            supervisor.start(ComponentKey.API, actor="local_cli")

        self.assertEqual(clock.sleeps, [0.25, 0.25])
        self.assertEqual(
            supervisor.status().components[ComponentKey.API].state,
            ComponentState.UNHEALTHY,
        )

    def test_disabled_tunnel_is_stopped_and_excluded_from_aggregate(self) -> None:
        processes = FakeProcessProvider()
        health = FakeHealthProvider()
        for component in ComponentKey:
            processes.set_process(component, running=True, owned=True)
            health.set_healthy(component, True)
        supervisor = LifecycleSupervisor(
            process_provider=processes,
            health_provider=health,
            enabled_components={ComponentKey.API, ComponentKey.WEB},
        )

        supervisor.reconcile()
        status = supervisor.status()

        self.assertEqual(status.aggregate_state, ComponentState.HEALTHY)
        self.assertEqual(
            status.components[ComponentKey.MCP_TUNNEL].state,
            ComponentState.STOPPED,
        )
        self.assertFalse(status.components[ComponentKey.MCP_TUNNEL].enabled)
        with self.assertRaises(LifecycleError):
            supervisor.restart(ComponentKey.MCP_TUNNEL, actor="local_cli")


if __name__ == "__main__":
    unittest.main()
