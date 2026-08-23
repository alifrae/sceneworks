"""LangGraph-based workflow orchestration for SceneWorks task pipelines.

The public WorkflowManager is the WP7 compatibility orchestrator. LangGraph
node topology remains an internal implementation detail; runtime persistence,
control commands and recovery policy live in focused modules.

Agents, backends, worktree services, and execution services must not depend
on this package. LangGraph is an orchestration dependency only.
"""

from app.workflows.orchestrator import WorkflowManager
from app.workflows.state import InitiativeState

__all__ = ["WorkflowManager", "InitiativeState"]
