"""SceneWorks MCP reasoning, engineering-control and PCS evidence interface.

Observe and Standard modes expose semantic SceneWorks concepts. Advanced mode
adds provider-neutral EngineeringSessions, durable WP15 evidence correlation and
WP16 PCS runtime semantics. Agent providers are optional workers; SceneWorks-
captured runtime/process/log/Git/PCS observations remain the evidence authority.
"""

from __future__ import annotations

from typing import Any

from app.mcp.server import MCPToolError
from app.mcp.wp16_server import PcsRuntimeMCPServer


class SceneWorksMCPServer(PcsRuntimeMCPServer):
    """Canonical WP16 server with provider-neutral PCS-control guidance."""

    def _wp16_instructions(self) -> str:
        if self.mode == "observe":
            mode_text = (
                "Observe mode: read-only semantic tools; PCS runtime configuration may be "
                "inspected but processes/actions are unavailable."
            )
        elif self.mode == "standard":
            mode_text = (
                "Standard mode: governed SceneWorks actions and host-visible Git project "
                "registration are enabled."
            )
        else:
            mode_text = (
                "Advanced mode: Standard tools plus task-bindable EngineeringSessions, "
                "durable evidence, direct workspace/command/process/Git control, and semantic "
                "PCS start/stop/restart/log/health/runtime-state/asset/runbook operations."
            )
        return (
            "SceneWorks is the engineering control plane and evidence authority. Ground "
            "reasoning in project state, accepted memory, task contracts, captured evidence, "
            "PCS runtime observations and Git truth. Provider/agent conclusions are inference, "
            "not authoritative evidence. Prefer PCS semantic/API control over GUI automation "
            "whenever deterministic PCS APIs are available. "
            + mode_text
        )

    def _require_advanced(self) -> None:
        if self.mode != "advanced":
            raise MCPToolError(
                "This tool requires explicit Advanced MCP mode. Advanced mode gives the MCP "
                "client SceneWorks-owned engineering and PCS runtime control with durable "
                "evidence; Gemini/OpenCode/OpenHands are optional workers."
            )

    async def handle(self, payload: Any) -> tuple[Any | None, int]:
        body, status = await super().handle(payload)
        if isinstance(payload, dict) and payload.get("method") in {
            "server/discover",
            "initialize",
        }:
            if isinstance(body, dict):
                result = body.get("result")
                if isinstance(result, dict):
                    result["instructions"] = self._wp16_instructions()
        return body, status


__all__ = ["SceneWorksMCPServer"]
