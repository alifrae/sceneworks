"""SceneWorks MCP reasoning and engineering-control interface.

Observe and Standard modes expose semantic SceneWorks concepts. Advanced mode
adds provider-neutral EngineeringSessions: direct worktree-confined filesystem,
command/process and Git capabilities owned by SceneWorks itself. Agent providers
(Gemini ACP, OpenCode, OpenHands) are optional delegated workers rather than the
execution substrate.
"""

from __future__ import annotations

from typing import Any

from app.mcp.server import MCPToolError
from app.mcp.wp14_server import ProviderNeutralMCPServer


class SceneWorksMCPServer(ProviderNeutralMCPServer):
    """Canonical WP14 server with provider-neutral discovery/error guidance."""

    def _wp14_instructions(self) -> str:
        if self.mode == "observe":
            mode_text = "Observe mode: read-only semantic tools; no executions or mutations."
        elif self.mode == "standard":
            mode_text = (
                "Standard mode: governed SceneWorks actions and host-visible Git project "
                "registration are enabled."
            )
        else:
            mode_text = (
                "Advanced mode: Standard tools plus provider-neutral EngineeringSessions "
                "with direct workspace, command/process and Git control. Agent delegation "
                "is optional and does not define the execution substrate."
            )
        return (
            "SceneWorks is the engineering control plane. Ground reasoning in project state, "
            "accepted memory, tasks, diffs and execution evidence. Direct machine capabilities "
            "are available only through an isolated EngineeringSession and its permission gates. "
            + mode_text
        )

    def _require_advanced(self) -> None:
        if self.mode != "advanced":
            raise MCPToolError(
                "This tool requires explicit Advanced MCP mode. Advanced mode gives the MCP "
                "client direct SceneWorks-owned engineering control in an isolated worktree; "
                "agent providers such as Gemini or OpenCode are optional delegated workers."
            )

    async def handle(self, payload: Any) -> tuple[Any | None, int]:
        body, status = await super().handle(payload)
        # The WP11 base protocol handler owns JSON-RPC framing and still emits
        # historical Gemini-specific instructions. Replace only the guidance;
        # leave protocol negotiation and batch behavior untouched.
        if isinstance(payload, dict) and payload.get("method") in {
            "server/discover",
            "initialize",
        }:
            if isinstance(body, dict):
                result = body.get("result")
                if isinstance(result, dict):
                    result["instructions"] = self._wp14_instructions()
        return body, status


__all__ = ["SceneWorksMCPServer"]
