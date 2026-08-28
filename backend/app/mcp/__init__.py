"""SceneWorks MCP reasoning interface.

The MCP boundary exposes SceneWorks semantics to external reasoning clients.
It deliberately does not expose raw filesystem, shell, SQL, or Git primitives;
repository execution remains mediated by the configured AgentBackend and
SceneWorks worktree/permission policy.
"""

from app.mcp.wp13_server import WorkManagementMCPServer as SceneWorksMCPServer

__all__ = ["SceneWorksMCPServer"]
