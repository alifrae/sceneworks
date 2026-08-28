"""SceneWorks MCP reasoning, engineering-control and evidence interface.

Observe and Standard modes expose semantic SceneWorks concepts. Advanced mode
adds provider-neutral EngineeringSessions and WP15 durable evidence/turn
correlation. Agent providers are optional workers; SceneWorks-captured runtime,
process, command, file and Git observations remain the evidence authority.
"""

from __future__ import annotations

from typing import Any

from app.mcp.server import MCPToolError
from app.mcp.wp15_server import EvidenceMCPServer


class SceneWorksMCPServer(EvidenceMCPServer):
    """Canonical WP15 server with provider-neutral discovery/error guidance."""

    def _wp15_instructions(self) -> str:
        if self.mode == "observe":
            mode_text = "Observe mode: read-only semantic tools; no executions or mutations."
        elif self.mode == "standard":
            mode_text = (
                "Standard mode: governed SceneWorks actions and host-visible Git project "
                "registration are enabled."
            )
        else:
            mode_text = (
                "Advanced mode: Standard tools plus task-bindable EngineeringSessions, "
                "explicit supervisor turns, direct workspace/command/process/Git control, "
                "and a durable SceneWorks evidence ledger. Agent delegation is optional."
            )
        return (
            "SceneWorks is the engineering control plane and evidence authority. Ground "
            "reasoning in project state, accepted memory, task contracts, captured evidence "
            "and Git truth. Provider/agent conclusions are inference, not authoritative "
            "evidence. Direct machine capabilities exist only through an isolated "
            "EngineeringSession and its permission gates. "
            + mode_text
        )

    def _require_advanced(self) -> None:
        if self.mode != "advanced":
            raise MCPToolError(
                "This tool requires explicit Advanced MCP mode. Advanced mode gives the MCP "
                "client direct SceneWorks-owned engineering control and durable evidence in "
                "an isolated worktree; Gemini/OpenCode/OpenHands are optional workers."
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
                    result["instructions"] = self._wp15_instructions()
        return body, status


__all__ = ["SceneWorksMCPServer"]
