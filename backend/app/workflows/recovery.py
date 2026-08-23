"""Workflow restart recovery policy (WP7).

Recovery is operational policy, not graph topology. Keeping it separate makes
restart behavior directly testable and prevents the orchestrator from owning
both normal execution and crash reconciliation.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.models import Task

logger = logging.getLogger("sceneworks.workflows.recovery")


class WorkflowRecovery:
    def __init__(self, owner: Any) -> None:
        self._owner = owner

    async def recover(self) -> list[int]:
        owner = self._owner
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
                await owner._emit_workflow_event(
                    task.id,
                    "workflow.interrupted",
                    {
                        "node": "architecture_approval",
                        "reason": "workflow paused — human approval required after restart",
                    },
                )
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
