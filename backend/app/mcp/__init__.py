"""SceneWorks MCP reasoning, engineering-control, PCS and GUI evidence interface.

Observe and Standard modes expose semantic SceneWorks concepts. Advanced mode
adds provider-neutral EngineeringSessions, durable WP15 evidence correlation,
WP16 PCS runtime semantics and WP17 observation-only GUI evidence. Agent
providers are optional workers; SceneWorks-captured runtime/process/log/Git/PCS
and screenshot observations remain the evidence authority.
"""

from __future__ import annotations

from typing import Any

from app.mcp.server import MCPToolError
from app.mcp.wp17_server import GuiEvidenceMCPServer


class SceneWorksMCPServer(GuiEvidenceMCPServer):
    """Canonical WP17 server with provider-neutral PCS/GUI evidence guidance."""

    def _wp17_instructions(self) -> str:
        if self.mode == "observe":
            mode_text = (
                "Observe mode: read-only semantic tools; PCS runtime configuration may be "
                "inspected but processes/actions/GUI capture are unavailable."
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
                "PCS control, and managed-PCS window/dialog/screenshot/visual-diff evidence."
            )
        return (
            "SceneWorks is the engineering control plane and evidence authority. Ground "
            "reasoning in project state, accepted memory, task contracts, captured evidence, "
            "PCS runtime observations, GUI artifacts and Git truth. Provider/agent conclusions "
            "and visual interpretation are inference, not authoritative evidence. Prefer PCS "
            "semantic/API control over GUI observation whenever deterministic PCS APIs are "
            "available. WP17 GUI support is observation-only and restricted to the managed PCS "
            "process; it does not expose focus, click, keyboard or arbitrary desktop capture. "
            + mode_text
        )

    def _require_advanced(self) -> None:
        if self.mode != "advanced":
            raise MCPToolError(
                "This tool requires explicit Advanced MCP mode. Advanced mode gives the MCP "
                "client SceneWorks-owned engineering, PCS runtime and managed GUI evidence "
                "capabilities; Gemini/OpenCode/OpenHands are optional workers."
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
                    result["instructions"] = self._wp17_instructions()
        return body, status


__all__ = ["SceneWorksMCPServer"]