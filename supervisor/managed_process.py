from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from supervisor.model import ComponentKey, ProcessObservation


class ProcessOwnershipError(RuntimeError):
    """Raised when a process cannot be proven to belong to SceneWorks."""


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    command_line: str


@dataclass(frozen=True)
class LaunchSpec:
    component: ComponentKey
    argv: tuple[str, ...]
    cwd: Path
    fingerprint: tuple[str, ...]
    env_overrides: dict[str, str] = field(default_factory=dict)
    adopt_port: int | None = None
    adopt_fingerprint: tuple[str, ...] | None = None


class ProcessHost(Protocol):
    def launch(self, spec: LaunchSpec) -> int: ...

    def inspect(self, pid: int) -> ProcessSnapshot | None: ...

    def terminate_tree(self, pid: int) -> None: ...


OwnershipSource = Literal["managed", "adopted"]


class ProcessMetadataStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, component: ComponentKey) -> int | None:
        record = self.get_record(component)
        return None if record is None else record[0]

    def get_record(self, component: ComponentKey) -> tuple[int, OwnershipSource] | None:
        with self._lock:
            raw = self._read()
            entry = raw.get(component.value)
            if not isinstance(entry, dict):
                return None
            pid = entry.get("pid")
            if not isinstance(pid, int) or pid <= 0:
                return None
            source = entry.get("source", "managed")
            if source not in {"managed", "adopted"}:
                return None
            return int(pid), source

    def set(
        self,
        component: ComponentKey,
        pid: int,
        *,
        source: OwnershipSource = "managed",
    ) -> None:
        if pid <= 0:
            raise ValueError("pid must be positive")
        if source not in {"managed", "adopted"}:
            raise ValueError("invalid ownership source")
        with self._lock:
            raw = self._read()
            raw[component.value] = {"pid": int(pid), "source": source}
            self._write(raw)

    def clear(self, component: ComponentKey) -> None:
        with self._lock:
            raw = self._read()
            raw.pop(component.value, None)
            self._write(raw)

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, object]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


class ManagedProcessProvider:
    def __init__(
        self,
        *,
        specs: dict[ComponentKey, LaunchSpec],
        store: ProcessMetadataStore,
        host: ProcessHost,
    ) -> None:
        self._specs = dict(specs)
        self._store = store
        self._host = host
        self._lock = threading.RLock()

    def observe(self, component: ComponentKey) -> ProcessObservation:
        with self._lock:
            spec = self._spec(component)
            record = self._store.get_record(component)
            if record is None:
                try:
                    adopted = self._try_adopt(spec)
                except (OSError, subprocess.SubprocessError):
                    return ProcessObservation(running=True, owned=False, pid=None)
                if adopted is None:
                    return ProcessObservation(running=False, owned=False, pid=None)
                record = (adopted, "adopted")
            pid, source = record

            try:
                snapshot = self._host.inspect(pid)
            except (OSError, subprocess.SubprocessError):
                return ProcessObservation(running=True, owned=False, pid=pid)
            if snapshot is None:
                return ProcessObservation(running=False, owned=True, pid=pid)
            fingerprint = (
                spec.adopt_fingerprint or spec.fingerprint
                if source == "adopted"
                else spec.fingerprint
            )
            return ProcessObservation(
                running=True,
                owned=self._matches(snapshot, fingerprint),
                pid=pid,
            )

    def start(self, component: ComponentKey) -> None:
        with self._lock:
            observation = self.observe(component)
            if observation.running:
                if observation.owned:
                    return
                raise ProcessOwnershipError(
                    f"refusing to start {component.value}: existing process ownership is ambiguous"
                )
            if observation.owned:
                self._store.clear(component)
            pid = self._host.launch(self._spec(component))
            self._store.set(component, pid, source="managed")

    def stop(self, component: ComponentKey) -> None:
        with self._lock:
            observation = self.observe(component)
            if observation.running and not observation.owned:
                raise ProcessOwnershipError(
                    f"refusing to stop {component.value}: process fingerprint mismatch"
                )
            if observation.running and observation.pid is not None:
                self._host.terminate_tree(observation.pid)
            self._store.clear(component)

    def _try_adopt(self, spec: LaunchSpec) -> int | None:
        if spec.adopt_port is None:
            return None
        finder = getattr(self._host, "find_listeners", None)
        if finder is None:
            return None
        fingerprint = spec.adopt_fingerprint or spec.fingerprint
        snapshots = [
            snapshot
            for snapshot in finder(spec.adopt_port)
            if self._matches(snapshot, fingerprint)
        ]
        if len(snapshots) != 1:
            return None
        pid = snapshots[0].pid
        self._store.set(spec.component, pid, source="adopted")
        return pid

    @staticmethod
    def _matches(snapshot: ProcessSnapshot, fingerprint: tuple[str, ...]) -> bool:
        command = snapshot.command_line.casefold()
        return bool(fingerprint) and all(token.casefold() in command for token in fingerprint)

    def _spec(self, component: ComponentKey) -> LaunchSpec:
        try:
            return self._specs[component]
        except KeyError as exc:
            raise ProcessOwnershipError(f"no launch spec for {component.value}") from exc


class WindowsProcessHost:
    CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)

    def launch(self, spec: LaunchSpec) -> int:
        env = os.environ.copy()
        env.update(spec.env_overrides)
        process = subprocess.Popen(
            list(spec.argv),
            cwd=str(spec.cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=self.CREATE_NEW_PROCESS_GROUP | self.DETACHED_PROCESS,
            close_fds=True,
        )
        return int(process.pid)

    def inspect(self, pid: int) -> ProcessSnapshot | None:
        if os.name != "nt":
            try:
                os.kill(pid, 0)
            except OSError:
                return None
            return ProcessSnapshot(pid=pid, command_line=str(pid))
        script = (
            "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = "
            f"{int(pid)}\" -ErrorAction SilentlyContinue;"
            "if($null -ne $p){[Console]::Out.Write($p.CommandLine)}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        command = result.stdout.strip()
        if result.returncode != 0 or not command:
            return None
        return ProcessSnapshot(pid=int(pid), command_line=command)

    def find_listeners(self, port: int) -> list[ProcessSnapshot]:
        if os.name != "nt":
            return []
        script = (
            f"$ids=@(Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
            "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);"
            "foreach($id in $ids){$p=Get-CimInstance Win32_Process -Filter \"ProcessId = $id\" "
            "-ErrorAction SilentlyContinue;if($null -ne $p){"
            "[Console]::Out.WriteLine(($p.ProcessId.ToString()+'|'+$p.CommandLine))}}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return []
        snapshots: list[ProcessSnapshot] = []
        for line in result.stdout.splitlines():
            pid_text, separator, command = line.partition("|")
            if not separator:
                continue
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            snapshots.append(ProcessSnapshot(pid=pid, command_line=command))
        return snapshots

    def terminate_tree(self, pid: int) -> None:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode not in {0, 128}:
                raise ProcessOwnershipError(
                    f"taskkill failed for pid {pid}: {result.stderr.strip()}"
                )
            return
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            return


__all__ = [
    "LaunchSpec",
    "ManagedProcessProvider",
    "ProcessHost",
    "ProcessMetadataStore",
    "ProcessOwnershipError",
    "ProcessSnapshot",
    "WindowsProcessHost",
]
