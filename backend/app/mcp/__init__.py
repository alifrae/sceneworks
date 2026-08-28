"""SceneWorks MCP reasoning interface (WP11).

The MCP boundary exposes SceneWorks semantics to external reasoning clients.
It deliberately does not expose raw filesystem, shell, SQL, or Git primitives;
repository execution remains mediated by the configured AgentBackend (normally
Gemini CLI over ACP) and SceneWorks worktree/permission policy.
"""

from app.mcp.attachments_server import AttachmentMCPServer as SceneWorksMCPServer

__all__ = ["SceneWorksMCPServer"]
