"""SceneWorks-owned execution runtimes.

Agent backends decide *what* work to do. Execution runtimes provide the
provider-neutral machine capabilities used by SceneWorks and MCP supervisors.
"""

from app.runtime.registry import RuntimeRegistry

__all__ = ["RuntimeRegistry"]
