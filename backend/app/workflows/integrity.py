"""Workflow recovery policy for control-plane integrity repairs."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.models import Task
from app.workflows.recovery import WorkflowRecovery

logger = logging.getLogger("sceneworks.workflows.integrity")


class IntegrityWorkflowRecovery(WorkflowRecovery):
    """Rehydrate durable human waits without manufacturing new interruptions."""

    async def recover(self) -> list[int]:
        owner: Any = self._owner
        resumed: list[int] = []

        async with owner._session_factory() as session:
            tasks = list(
                (
                    await session.execute(
                        select(Task).where(
                            Task.status.in_(
                                [
                                    "AWAITING_ARCHITECTURE_APPROVAL",
                                    "READY_TO_IMPLEMENT",
                                    "CHANGES_REQUESTED",
                                ]
                            )
                        )
                    )
                ).scalars()
            )

        for task in tasks:
            checkpointer = await owner._ensure_checkpointer()
            config = owner._config(task.id)
            try:
                state = await checkpointer.aget(config)
            except Exception:  # noqa: BLE001 - one corrupt checkpoint must not block startup
                logger.warning("could not inspect workflow checkpoint for task %s", task.id)
                continue
            if state is None:
                continue

            if task.status == "AWAITING_ARCHITECTURE_APPROVAL":
                # This is rehydration of an already-persisted wait, not a new
                # transition into the approval node. The graph emitted the
                # interruption when the wait instance was first created.
                logger.debug("rehydrated approval wait for task %s", task.id)
                continue

            if task.status == "READY_TO_IMPLEMENT":
                if not task.architecture_result:
                    continue
                try:
                    await owner.start_implementation(task.id)
                    resumed.append(task.id)
                    logger.info("auto-resumed task %s from READY_TO_IMPLEMENT", task.id)
                except Exception as exc:  # noqa: BLE001 - recovery is best effort per task
                    logger.warning("could not auto-resume task %s: %s", task.id, exc)
                continue

            if task.status == "CHANGES_REQUESTED":
                try:
                    await owner.start_implementation(task.id)
                    resumed.append(task.id)
                    logger.info(
                        "auto-resumed task %s from CHANGES_REQUESTED (repair)",
                        task.id,
                    )
                except Exception as exc:  # noqa: BLE001 - recovery is best effort per task
                    logger.warning(
                        "could not auto-resume repair for task %s: %s",
                        task.id,
                        exc,
                    )

        return resumed
