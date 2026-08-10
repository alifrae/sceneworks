"""LangGraph-based workflow orchestration for SceneWorks task pipelines.

The workflow package replaces the manual continuation hooks in
services/workflow.py with a typed, checkpointed LangGraph state graph.

Agents, backends, worktree services, and execution services must not depend
on this package. LangGraph is an orchestration dependency only.
"""

from app.workflows.manager import WorkflowManager
from app.workflows.state import InitiativeState

__all__ = ["WorkflowManager", "InitiativeState"]
