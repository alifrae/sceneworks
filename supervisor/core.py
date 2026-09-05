from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from supervisor.model import (
    DEFAULT_COMPONENT_SPECS,
    ComponentKey,
    ComponentSpec,
    ComponentState,
    ComponentStatus,
    SupervisorStatus,
)
from supervisor.providers import HealthProvider, ProcessProvider


class LifecycleError(RuntimeError):
    """Expected lifecycle-domain failure."""


AutoRecoverySink = Callable[[ComponentKey, Callable[[], None]], None]


@dataclass
class _ComponentRecord:
    state: ComponentState = ComponentState.UNKNOWN
    consecutive_failures: int = 0
    restart_history: deque[float] = field(default_factory=deque)
    last_transition_at: float = 0.0
    healthy_since: float | None = None


class LifecycleSupervisor:
    MONITOR_FAILURE_THRESHOLD = 3
    RESTART_WINDOW_SECONDS = 300.0
    HEALTHY_RESET_SECONDS = 600.0
    RETRY_DELAYS = (1.0, 2.0, 5.0)

    def __init__(
        self,
        *,
        process_provider: ProcessProvider,
        health_provider: HealthProvider,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        auto_recovery_sink: AutoRecoverySink | None = None,
        component_specs: dict[ComponentKey, ComponentSpec] | None = None,
        startup_poll_seconds: float = 0.25,
        enabled_components: set[ComponentKey] | None = None,
    ) -> None:
        self._process = process_provider
        self._health = health_provider
        self._clock = clock
        self._sleep = sleep
        self._auto_recovery_sink = auto_recovery_sink
        self._component_specs = dict(component_specs or DEFAULT_COMPONENT_SPECS)
        self._startup_poll_seconds = max(float(startup_poll_seconds), 0.01)
        self._enabled_components = set(ComponentKey) if enabled_components is None else set(enabled_components)
        now = self._clock()
        self._records = {
            component: _ComponentRecord(last_transition_at=now)
            for component in ComponentKey
        }
        self._lock = threading.RLock()

    def set_auto_recovery_sink(self, sink: AutoRecoverySink | None) -> None:
        with self._lock:
            self._auto_recovery_sink = sink

    def status(self) -> SupervisorStatus:
        with self._lock:
            now = self._clock()
            components: dict[ComponentKey, ComponentStatus] = {}
            for component, record in self._records.items():
                self._prune_restart_history(record, now)
                components[component] = ComponentStatus(
                    component=component,
                    state=record.state,
                    consecutive_failures=record.consecutive_failures,
                    restart_attempts=len(record.restart_history),
                    last_transition_at=record.last_transition_at,
                    healthy_since=record.healthy_since,
                    enabled=component in self._enabled_components,
                )

            active_states = {
                row.state for row in components.values() if row.enabled
            }
            if not active_states:
                aggregate = ComponentState.STOPPED
            elif active_states == {ComponentState.HEALTHY}:
                aggregate = ComponentState.HEALTHY
            elif ComponentState.DEGRADED in active_states:
                aggregate = ComponentState.DEGRADED
            else:
                aggregate = ComponentState.UNHEALTHY
            return SupervisorStatus(aggregate_state=aggregate, components=components)

    def reconcile(self) -> SupervisorStatus:
        with self._lock:
            for component in ComponentKey:
                if component not in self._enabled_components:
                    self._records[component].consecutive_failures = 0
                    self._records[component].healthy_since = None
                    self._mark(component, ComponentState.STOPPED)
                    continue

                observation = self._process.observe(component)
                if observation.running and not observation.owned:
                    self._mark(component, ComponentState.UNKNOWN)
                    continue
                if observation.running:
                    if self._health.healthy(component):
                        self._mark_healthy(component)
                    else:
                        self._mark(component, ComponentState.UNHEALTHY)
                    continue

                endpoint_healthy = self._health.healthy(component)
                if endpoint_healthy:
                    self._records[component].healthy_since = None
                    self._mark(component, ComponentState.UNKNOWN)
                elif observation.owned:
                    self._mark(component, ComponentState.UNHEALTHY)
                else:
                    self._mark(component, ComponentState.STOPPED)
            return self.status()

    def start(self, component: ComponentKey, *, actor: str) -> None:
        del actor
        with self._lock:
            self._require_enabled(component)
            observation = self._process.observe(component)
            if observation.running:
                if not observation.owned:
                    self._mark(component, ComponentState.UNKNOWN)
                    raise LifecycleError(
                        f"cannot start {component.value}: running process ownership is ambiguous"
                    )
                if self._health.healthy(component):
                    self._mark_healthy(component)
                    return
                self._mark(component, ComponentState.UNHEALTHY)
                raise LifecycleError(
                    f"cannot start {component.value}: owned process is already running but unhealthy"
                )

            if self._health.healthy(component):
                self._mark(component, ComponentState.UNKNOWN)
                raise LifecycleError(
                    f"cannot start {component.value}: endpoint is reachable without owned process"
                )

            self._mark(component, ComponentState.STARTING)
            self._process.start(component)
            if self._wait_until_healthy(component):
                self._mark_healthy(component)
                return
            self._mark(component, ComponentState.UNHEALTHY)
            raise LifecycleError(f"{component.value} did not become healthy")

    def stop(self, component: ComponentKey, *, actor: str) -> None:
        del actor
        with self._lock:
            observation = self._process.observe(component)
            if not observation.running:
                if self._health.healthy(component):
                    self._mark(component, ComponentState.UNKNOWN)
                    raise LifecycleError(
                        f"refusing to stop {component.value}: endpoint ownership is ambiguous"
                    )
                self._mark(component, ComponentState.STOPPED)
                return
            if not observation.owned:
                self._mark(component, ComponentState.UNKNOWN)
                raise LifecycleError(
                    f"refusing to stop {component.value}: process ownership is ambiguous"
                )
            self._process.stop(component)
            self._records[component].consecutive_failures = 0
            self._records[component].healthy_since = None
            self._mark(component, ComponentState.STOPPED)

    def restart(self, component: ComponentKey, *, actor: str) -> None:
        with self._lock:
            self._require_enabled(component)
            observation = self._process.observe(component)
            if observation.running:
                self.stop(component, actor=actor)
            elif self._health.healthy(component):
                self._mark(component, ComponentState.UNKNOWN)
                raise LifecycleError(
                    f"refusing to restart {component.value}: endpoint ownership is ambiguous"
                )
            self.start(component, actor=actor)

    def restart_all(self, *, actor: str) -> None:
        with self._lock:
            for component in (
                ComponentKey.MCP_TUNNEL,
                ComponentKey.WEB,
                ComponentKey.API,
            ):
                if component in self._enabled_components:
                    self.stop(component, actor=actor)
            for component in (
                ComponentKey.API,
                ComponentKey.WEB,
                ComponentKey.MCP_TUNNEL,
            ):
                if component in self._enabled_components:
                    self.start(component, actor=actor)

    def monitor_once(self) -> SupervisorStatus:
        with self._lock:
            for component in ComponentKey:
                record = self._records[component]
                if component not in self._enabled_components:
                    record.consecutive_failures = 0
                    record.healthy_since = None
                    self._mark(component, ComponentState.STOPPED)
                    continue

                observation = self._process.observe(component)

                if observation.running and not observation.owned:
                    record.healthy_since = None
                    self._mark(component, ComponentState.UNKNOWN)
                    continue

                if not observation.running:
                    if self._health.healthy(component):
                        record.healthy_since = None
                        record.consecutive_failures = 0
                        self._mark(component, ComponentState.UNKNOWN)
                        continue
                    if record.state == ComponentState.STOPPED and not observation.owned:
                        continue
                    if observation.owned and record.state != ComponentState.STOPPED:
                        self._request_recovery(component)
                    elif record.state != ComponentState.STOPPED:
                        record.healthy_since = None
                        self._mark(component, ComponentState.UNHEALTHY)
                    continue

                if self._health.healthy(component):
                    record.consecutive_failures = 0
                    if record.healthy_since is None:
                        record.healthy_since = self._clock()
                    if (
                        record.restart_history
                        and self._clock() - record.healthy_since >= self.HEALTHY_RESET_SECONDS
                    ):
                        record.restart_history.clear()
                    self._mark(component, ComponentState.HEALTHY)
                    continue

                record.healthy_since = None
                record.consecutive_failures += 1
                self._mark(component, ComponentState.UNHEALTHY)
                if record.consecutive_failures >= self.MONITOR_FAILURE_THRESHOLD:
                    self._request_recovery(component)

            return self.status()

    def _request_recovery(self, component: ComponentKey) -> None:
        sink = self._auto_recovery_sink
        if sink is None:
            self._recover(component)
            return
        sink(component, lambda: self._recover(component))

    def _recover(self, component: ComponentKey) -> None:
        if component not in self._enabled_components:
            self._mark(component, ComponentState.STOPPED)
            return
        record = self._records[component]
        now = self._clock()
        self._prune_restart_history(record, now)
        if len(record.restart_history) >= len(self.RETRY_DELAYS):
            self._mark(component, ComponentState.DEGRADED)
            return

        while len(record.restart_history) < len(self.RETRY_DELAYS):
            attempt_index = len(record.restart_history)
            self._sleep(self.RETRY_DELAYS[attempt_index])
            now = self._clock()
            self._prune_restart_history(record, now)
            if len(record.restart_history) >= len(self.RETRY_DELAYS):
                self._mark(component, ComponentState.DEGRADED)
                return
            record.restart_history.append(now)
            self._mark(component, ComponentState.RECOVERING)

            observation = self._process.observe(component)
            if observation.running:
                if not observation.owned:
                    self._mark(component, ComponentState.DEGRADED)
                    return
                self._process.stop(component)
            elif self._health.healthy(component):
                self._mark(component, ComponentState.UNKNOWN)
                return

            self._process.start(component)
            if self._health.healthy(component):
                record.consecutive_failures = 0
                record.healthy_since = self._clock()
                self._mark(component, ComponentState.HEALTHY)
                return

            record.healthy_since = None
            self._mark(component, ComponentState.UNHEALTHY)

        self._mark(component, ComponentState.DEGRADED)

    def _wait_until_healthy(self, component: ComponentKey) -> bool:
        spec = self._component_specs.get(component, DEFAULT_COMPONENT_SPECS[component])
        deadline = self._clock() + max(float(spec.startup_grace_seconds), 0.0)
        while True:
            if self._health.healthy(component):
                return True
            now = self._clock()
            if now >= deadline:
                return False
            self._sleep(min(self._startup_poll_seconds, deadline - now))

    def _require_enabled(self, component: ComponentKey) -> None:
        if component not in self._enabled_components:
            self._mark(component, ComponentState.STOPPED)
            raise LifecycleError(f"{component.value} is disabled")

    def _mark_healthy(self, component: ComponentKey) -> None:
        record = self._records[component]
        record.consecutive_failures = 0
        if record.healthy_since is None:
            record.healthy_since = self._clock()
        self._mark(component, ComponentState.HEALTHY)

    def _mark(self, component: ComponentKey, state: ComponentState) -> None:
        record = self._records[component]
        if record.state != state:
            record.state = state
            record.last_transition_at = self._clock()

    def _prune_restart_history(self, record: _ComponentRecord, now: float) -> None:
        while record.restart_history and now - record.restart_history[0] > self.RESTART_WINDOW_SECONDS:
            record.restart_history.popleft()


__all__ = ["AutoRecoverySink", "LifecycleError", "LifecycleSupervisor"]
