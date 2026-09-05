"""SceneWorks MCP reasoning, engineering-control, PCS, GUI and system interface."""

from __future__ import annotations

from typing import Any

from app.mcp.integrity import ControlPlaneIntegrityMCPServer
from app.mcp.server import MCPToolError, _bounded, _tool
from app.services.supervisor import SupervisorUnavailable


class SceneWorksMCPServer(ControlPlaneIntegrityMCPServer):
    """Canonical server with provider-neutral engineering and lifecycle controls."""

    def _wp18_instructions(self) -> str:
        if self.mode == "observe":
            mode_text = (
                "Observe mode: read-only semantic tools; PCS runtime configuration may be "
                "inspected but processes/actions/GUI capture and automation are unavailable."
            )
        elif self.mode == "standard":
            mode_text = (
                "Standard mode: governed SceneWorks actions and host-visible Git project "
                "registration are enabled."
            )
        else:
            mode_text = (
                "Advanced mode: Standard tools plus task-bindable EngineeringSessions, "
                "durable evidence, direct workspace/command/process/Git control, semantic "
                "PCS control, managed-PCS visual evidence and permission-gated Windows UI "
                "Automation controls."
            )
        return (
            "SceneWorks is the engineering control plane and evidence authority. Ground "
            "reasoning in project state, accepted memory, task contracts, captured evidence, "
            "PCS runtime observations, GUI artifacts and Git truth. Provider/agent conclusions "
            "and visual interpretation are inference, not authoritative evidence. Prefer PCS "
            "semantic/API control over GUI automation whenever deterministic PCS APIs are "
            "available. WP18 GUI mutation is a fallback: it requires gui_observe plus "
            "gui_automate, resolves opaque UI Automation control ids only inside the current "
            "SceneWorks-managed PCS window, never uses caller-supplied screen coordinates, and "
            "requires before/after screenshot evidence with deterministic visual comparison. "
            "WP21 infrastructure lifecycle mutation is semantic and supervisor-owned; MCP may "
            "request only api/web/mcp_tunnel/all restart operations and never receives raw PID, "
            "port, command, executable, environment, or shell authority. "
            + mode_text
        )

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
        read_only = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        }
        lifecycle_action = {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        }
        tools.extend(
            [
                _tool(
                    "sceneworks.system.status",
                    "Return bounded local SceneWorks API/web/MCP-tunnel lifecycle state from the out-of-process supervisor.",
                    {},
                    read_only,
                ),
                _tool(
                    "sceneworks.system.restart",
                    "Request a journaled semantic restart of one SceneWorks infrastructure component or the complete stack. No PID, port, path, URL, command or environment input is accepted.",
                    {
                        "component": {
                            "type": "string",
                            "enum": ["api", "web", "mcp_tunnel", "all"],
                        }
                    },
                    lifecycle_action,
                    required=["component"],
                ),
            ]
        )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        if name not in {"sceneworks.system.status", "sceneworks.system.restart"}:
            return await super().call_tool(name, arguments)
        args = arguments or {}
        if self.ctx.supervisor is None:
            raise MCPToolError("local SceneWorks lifecycle supervisor is unavailable")
        try:
            if name == "sceneworks.system.status":
                if args:
                    raise MCPToolError("sceneworks.system.status accepts no arguments")
                result = await self.ctx.supervisor.status()
            else:
                if set(args) != {"component"}:
                    raise MCPToolError("sceneworks.system.restart accepts only component")
                component = str(args.get("component") or "")
                if component not in {"api", "web", "mcp_tunnel", "all"}:
                    raise MCPToolError("component must be api, web, mcp_tunnel, or all")
                result = await self.ctx.supervisor.restart(component, actor="mcp")
        except SupervisorUnavailable as exc:
            raise MCPToolError(str(exc)) from exc
        return _bounded(result, int(self.ctx.settings.mcp_tool_max_chars))

    def _require_advanced(self) -> None:
        if self.mode != "advanced":
            raise MCPToolError(
                "This tool requires explicit Advanced MCP mode. Advanced mode gives the MCP "
                "client SceneWorks-owned engineering, PCS runtime and managed GUI capabilities; "
                "Gemini/OpenCode/OpenHands are optional workers."
            )

    async def _engineering_session_close(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = int(args.get("session_id"))
        if await self.ctx.pcs_control.has_active_run(session_id):
            raise MCPToolError(
                f"engineering session {session_id} still owns an active managed PCS run; "
                "call sceneworks.pcs.stop before closing the session"
            )
        return await super()._engineering_session_close(args)

    async def handle(self, payload: Any) -> tuple[Any | None, int]:
        body, status = await super().handle(payload)
        if isinstance(payload, dict) and payload.get("method") in {
            "server/discover",
            "initialize",
        }:
            if isinstance(body, dict):
                result = body.get("result")
                if isinstance(result, dict):
                    result["instructions"] = self._wp18_instructions()
        return body, status


__all__ = ["SceneWorksMCPServer"]
