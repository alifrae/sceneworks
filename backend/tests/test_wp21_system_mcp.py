from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.mcp import SceneWorksMCPServer
from app.mcp.integrity import ControlPlaneIntegrityMCPServer
from app.mcp.server import MCPToolError


class FakeSupervisor:
    def __init__(self) -> None:
        self.restarts: list[tuple[str, str]] = []

    async def status(self) -> dict:
        return {"aggregate_state": "HEALTHY", "components": {}}

    async def restart(self, component: str, *, actor: str = "mcp", correlation_id=None) -> dict:
        del correlation_id
        self.restarts.append((component, actor))
        return {"operation_id": "op-1"}


def _server(supervisor=None) -> SceneWorksMCPServer:
    ctx = SimpleNamespace(
        settings=SimpleNamespace(effective_mcp_mode="observe", mcp_tool_max_chars=10000),
        supervisor=supervisor,
    )
    return SceneWorksMCPServer(ctx)


def test_system_tools_are_available_in_every_mode_and_have_narrow_schema(monkeypatch) -> None:
    monkeypatch.setattr(ControlPlaneIntegrityMCPServer, "tool_definitions", lambda self: [])
    server = _server(FakeSupervisor())
    tools = {tool["name"]: tool for tool in server.tool_definitions()}
    assert {"sceneworks.system.status", "sceneworks.system.restart"} <= set(tools)
    restart_schema = tools["sceneworks.system.restart"]["inputSchema"]
    properties = restart_schema["properties"]
    assert set(properties) == {"component"}
    assert properties["component"]["enum"] == ["api", "web", "mcp_tunnel", "all"]
    serialized = str(restart_schema).lower()
    for forbidden in ("pid", "port", "path", "url", "command", "environment"):
        assert forbidden not in serialized


def test_restart_dispatches_semantic_component_only() -> None:
    supervisor = FakeSupervisor()
    server = _server(supervisor)
    result = asyncio.run(server.call_tool("sceneworks.system.restart", {"component": "web"}))
    assert result["operation_id"] == "op-1"
    assert supervisor.restarts == [("web", "mcp")]


def test_restart_rejects_extra_machine_control_fields() -> None:
    server = _server(FakeSupervisor())
    with pytest.raises(MCPToolError, match="accepts only component"):
        asyncio.run(
            server.call_tool(
                "sceneworks.system.restart",
                {"component": "api", "pid": 123},
            )
        )


def test_unavailable_supervisor_is_bounded_tool_error() -> None:
    server = _server(None)
    with pytest.raises(MCPToolError, match="supervisor is unavailable"):
        asyncio.run(server.call_tool("sceneworks.system.status", {}))
