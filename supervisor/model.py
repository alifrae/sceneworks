from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ComponentKey(StrEnum):
    API = "api"
    WEB = "web"
    MCP_TUNNEL = "mcp_tunnel"


class ComponentState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    RECOVERING = "RECOVERING"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class OperationResult(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ProcessObservation:
    running: bool
    owned: bool
    pid: int | None = None


@dataclass(frozen=True)
class ComponentSpec:
    key: ComponentKey
    startup_grace_seconds: float


@dataclass(frozen=True)
class ComponentStatus:
    component: ComponentKey
    state: ComponentState
    consecutive_failures: int
    restart_attempts: int
    last_transition_at: float
    healthy_since: float | None


@dataclass(frozen=True)
class SupervisorStatus:
    aggregate_state: ComponentState
    components: dict[ComponentKey, ComponentStatus]


DEFAULT_COMPONENT_SPECS: dict[ComponentKey, ComponentSpec] = {
    ComponentKey.API: ComponentSpec(ComponentKey.API, 45.0),
    ComponentKey.WEB: ComponentSpec(ComponentKey.WEB, 60.0),
    ComponentKey.MCP_TUNNEL: ComponentSpec(ComponentKey.MCP_TUNNEL, 20.0),
}
