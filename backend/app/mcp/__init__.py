"""SceneWorks MCP reasoning and engineering-control interface.

Observe and Standard modes expose semantic SceneWorks concepts. Advanced mode
adds provider-neutral EngineeringSessions: direct worktree-confined filesystem,
command/process and Git capabilities owned by SceneWorks itself. Agent providers
(Gemini ACP, OpenCode, OpenHands) are optional delegated workers rather than the
execution substrate.
"""

from app.mcp.wp14_server import ProviderNeutralMCPServer as SceneWorksMCPServer

__all__ = ["SceneWorksMCPServer"]
