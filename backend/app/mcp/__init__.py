"""SceneWorks MCP reasoning, engineering-control, PCS and GUI interface.

Observe and Standard modes expose semantic SceneWorks concepts. Advanced mode
adds provider-neutral EngineeringSessions, durable WP15 evidence correlation,
WP16 PCS runtime semantics, WP17 GUI evidence and WP18 controlled accessibility
automation. Agent providers are optional workers; SceneWorks-captured runtime,
process, log, Git, PCS and GUI observations remain the evidence authority.
"""

from __future__ import annotations

from typing import Any

from app.mcp.integrity import ControlPlaneIntegrityMCPServer
from app.mcp.server import MCPToolError


class SceneWorksMCPServer(ControlPlaneIntegrityMCPServer):
    """Canonical server with provider-neutral PCS/GUI and integrity guidance."""

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
            + mode_text
        )

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
