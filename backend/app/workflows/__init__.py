"""LangGraph-based workflow orchestration for SceneWorks task pipelines.

The public WorkflowManager is the WP11 adaptive compatibility orchestrator.
LangGraph node topology remains an internal implementation detail; runtime
persistence, control commands and recovery policy live in focused modules.

Agents, backends, worktree services, and execution services must not depend
on this package. LangGraph is an orchestration dependency only.
"""

from sqlalchemy import select

from app.models import Artifact, Execution
from app.services.workflow import ASK_ALLOWED_ROLES
from app.workflows.adaptive import WorkflowManager as AdaptiveWorkflowManager
from app.workflows.integrity import IntegrityWorkflowRecovery
from app.workflows.state import InitiativeState


class WorkflowManager(AdaptiveWorkflowManager):
    """Canonical workflow manager with restart/artifact integrity guarantees."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._recovery = IntegrityWorkflowRecovery(self)

    async def on_execution_finished(self, execution_id: str) -> None:
        """Persist task-less role artifacts exactly once per execution."""
        async with self._session_factory() as session:
            execution = await session.get(Execution, execution_id)
            if execution is not None and execution.task_id is None:
                if execution.role in ASK_ALLOWED_ROLES:
                    existing = (
                        await session.execute(
                            select(Artifact.id).where(
                                Artifact.source_execution_id == execution_id
                            ).limit(1)
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        return
        await super().on_execution_finished(execution_id)


__all__ = ["WorkflowManager", "InitiativeState"]
