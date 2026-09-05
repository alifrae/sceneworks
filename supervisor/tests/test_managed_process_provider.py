from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from supervisor.managed_process import (
    LaunchSpec,
    ManagedProcessProvider,
    ProcessMetadataStore,
    ProcessOwnershipError,
    ProcessSnapshot,
)
from supervisor.model import ComponentKey


class FakeProcessHost:
    def __init__(self) -> None:
        self.next_pid = 100
        self.processes: dict[int, ProcessSnapshot] = {}
        self.listeners: dict[int, list[ProcessSnapshot]] = {}
        self.launches: list[LaunchSpec] = []
        self.terminations: list[int] = []

    def launch(self, spec: LaunchSpec) -> int:
        self.launches.append(spec)
        pid = self.next_pid
        self.next_pid += 1
        self.processes[pid] = ProcessSnapshot(
            pid=pid,
            command_line=" ".join(spec.argv),
        )
        return pid

    def inspect(self, pid: int) -> ProcessSnapshot | None:
        return self.processes.get(pid)

    def find_listeners(self, port: int) -> list[ProcessSnapshot]:
        return list(self.listeners.get(port, []))

    def terminate_tree(self, pid: int) -> None:
        self.terminations.append(pid)
        self.processes.pop(pid, None)


class ManagedProcessProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_path = Path(self.tmp.name) / "processes.json"
        self.store = ProcessMetadataStore(self.store_path)
        self.host = FakeProcessHost()
        self.specs = {
            ComponentKey.API: LaunchSpec(
                component=ComponentKey.API,
                argv=("uv", "run", "python", "-m", "app.main"),
                cwd=Path("backend"),
                fingerprint=("uv", "app.main"),
                env_overrides={"CONTROL_PLANE_API_KEY": "must-not-persist"},
            )
        }
        self.provider = ManagedProcessProvider(
            specs=self.specs,
            store=self.store,
            host=self.host,
        )

    def test_start_persists_pid_and_reconciles_after_provider_restart(self) -> None:
        self.provider.start(ComponentKey.API)
        first = self.provider.observe(ComponentKey.API)
        self.assertTrue(first.running)
        self.assertTrue(first.owned)
        self.assertEqual(first.pid, 100)

        reopened = ManagedProcessProvider(
            specs=self.specs,
            store=ProcessMetadataStore(self.store_path),
            host=self.host,
        )
        second = reopened.observe(ComponentKey.API)
        self.assertEqual(second, first)

    def test_natural_exit_remains_known_as_previously_owned(self) -> None:
        self.provider.start(ComponentKey.API)
        self.host.processes.pop(100)

        observation = self.provider.observe(ComponentKey.API)

        self.assertFalse(observation.running)
        self.assertTrue(observation.owned)
        self.assertEqual(observation.pid, 100)

    def test_pid_reuse_or_fingerprint_mismatch_fails_closed(self) -> None:
        self.provider.start(ComponentKey.API)
        self.host.processes[100] = ProcessSnapshot(
            pid=100,
            command_line="unrelated.exe --serve",
        )

        observation = self.provider.observe(ComponentKey.API)
        self.assertTrue(observation.running)
        self.assertFalse(observation.owned)

        with self.assertRaises(ProcessOwnershipError):
            self.provider.stop(ComponentKey.API)
        self.assertEqual(self.host.terminations, [])

    def test_owned_stop_terminates_tree_and_clears_metadata(self) -> None:
        self.provider.start(ComponentKey.API)
        self.provider.stop(ComponentKey.API)

        self.assertEqual(self.host.terminations, [100])
        self.assertFalse(self.provider.observe(ComponentKey.API).running)
        self.assertFalse(self.provider.observe(ComponentKey.API).owned)

    def test_metadata_never_persists_environment_values(self) -> None:
        self.provider.start(ComponentKey.API)
        raw = self.store_path.read_text(encoding="utf-8")

        self.assertNotIn("must-not-persist", raw)
        self.assertNotIn("CONTROL_PLANE_API_KEY", raw)
        parsed = json.loads(raw)
        self.assertEqual(parsed["api"]["pid"], 100)
        self.assertEqual(parsed["api"]["source"], "managed")

    def test_legacy_adoption_uses_separate_listener_fingerprint(self) -> None:
        spec = LaunchSpec(
            component=ComponentKey.API,
            argv=("uv", "run", "python", "-m", "app.main"),
            cwd=Path("backend"),
            fingerprint=("uv", "app.main"),
            adopt_port=8010,
            adopt_fingerprint=("app.main",),
        )
        self.host.listeners[8010] = [
            ProcessSnapshot(pid=222, command_line="python.exe -m app.main")
        ]
        self.host.processes[222] = self.host.listeners[8010][0]
        provider = ManagedProcessProvider(
            specs={ComponentKey.API: spec},
            store=ProcessMetadataStore(self.store_path),
            host=self.host,
        )

        observation = provider.observe(ComponentKey.API)
        reopened = ManagedProcessProvider(
            specs={ComponentKey.API: spec},
            store=ProcessMetadataStore(self.store_path),
            host=self.host,
        )
        after_restart = reopened.observe(ComponentKey.API)

        self.assertTrue(observation.running)
        self.assertTrue(observation.owned)
        self.assertEqual(observation.pid, 222)
        self.assertEqual(after_restart, observation)
        parsed = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["api"]["source"], "adopted")

    def test_legacy_adoption_still_rejects_unrelated_listener(self) -> None:
        spec = LaunchSpec(
            component=ComponentKey.API,
            argv=("uv", "run", "python", "-m", "app.main"),
            cwd=Path("backend"),
            fingerprint=("uv", "app.main"),
            adopt_port=8010,
            adopt_fingerprint=("app.main",),
        )
        self.host.listeners[8010] = [
            ProcessSnapshot(pid=333, command_line="unrelated.exe --port 8010")
        ]
        provider = ManagedProcessProvider(
            specs={ComponentKey.API: spec},
            store=ProcessMetadataStore(self.store_path),
            host=self.host,
        )

        observation = provider.observe(ComponentKey.API)

        self.assertFalse(observation.running)
        self.assertFalse(observation.owned)
        self.assertIsNone(observation.pid)

    def test_adoption_discovery_timeout_fails_closed_without_crashing(self) -> None:
        class TimeoutListenerHost(FakeProcessHost):
            def find_listeners(self, port: int) -> list[ProcessSnapshot]:
                raise subprocess.TimeoutExpired(["powershell.exe"], 5)

        spec = LaunchSpec(
            component=ComponentKey.API,
            argv=("uv", "run", "python", "-m", "app.main"),
            cwd=Path("backend"),
            fingerprint=("uv", "app.main"),
            adopt_port=8010,
            adopt_fingerprint=("app.main",),
        )
        provider = ManagedProcessProvider(
            specs={ComponentKey.API: spec},
            store=ProcessMetadataStore(self.store_path),
            host=TimeoutListenerHost(),
        )

        try:
            observation = provider.observe(ComponentKey.API)
        except subprocess.TimeoutExpired as exc:
            self.fail(f"listener discovery timeout escaped reconciliation: {exc}")

        self.assertTrue(observation.running)
        self.assertFalse(observation.owned)
        self.assertIsNone(observation.pid)

    def test_process_inspection_timeout_fails_closed_without_crashing(self) -> None:
        class TimeoutInspectHost(FakeProcessHost):
            def inspect(self, pid: int) -> ProcessSnapshot | None:
                raise subprocess.TimeoutExpired(["powershell.exe"], 5)

        store = ProcessMetadataStore(self.store_path)
        store.set(ComponentKey.API, 444, source="managed")
        provider = ManagedProcessProvider(
            specs=self.specs,
            store=store,
            host=TimeoutInspectHost(),
        )

        try:
            observation = provider.observe(ComponentKey.API)
        except subprocess.TimeoutExpired as exc:
            self.fail(f"process inspection timeout escaped reconciliation: {exc}")

        self.assertTrue(observation.running)
        self.assertFalse(observation.owned)
        self.assertEqual(observation.pid, 444)


if __name__ == "__main__":
    unittest.main()
