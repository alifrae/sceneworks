"""WP15 completion layer: execution-event aggregation and evidence status semantics."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models import Event
from app.mcp.wp15_server import EvidenceMCPServer


class CompleteEvidenceMCPServer(EvidenceMCPServer):
    """Expose one correlated history across direct actions and delegated agents."""

    async def _events(self, args: dict[str, Any]) -> dict[str, Any]:
        result = await super()._events(args)
        execution_ids = {
            str(event.get("payload", {}).get("execution_id"))
            for event in result.get("events", [])
            if event.get("category") == "agent"
            and event.get("payload", {}).get("execution_id")
        }
        agent_events: list[dict[str, Any]] = []
        if execution_ids:
            limit = max(1, min(500, int(args.get("limit") or 100)))
            async with self.ctx.engine_factory() as session:
                rows = list(
                    (
                        await session.execute(
                            select(Event)
                            .where(Event.execution_id.in_(sorted(execution_ids)))
                            .order_by(Event.id.asc())
                            .limit(limit)
                        )
                    ).scalars().all()
                )
            agent_events = [
                {
                    "id": row.id,
                    "execution_id": row.execution_id,
                    "task_id": row.task_id,
                    "type": row.type,
                    "payload": dict(row.payload or {}),
                    "severity": row.severity,
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                }
                for row in rows
            ]
        return {**result, "agent_events": agent_events}

    def _success_payload(
        self,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        before: dict[str, Any],
    ) -> tuple[str, str, str, dict[str, Any]]:
        category, operation, status, payload = super()._success_payload(
            name, args, result, before
        )
        if name == "sceneworks.command.run":
            exit_code = result.get("returncode")
            status = "COMPLETED" if exit_code == 0 else "FAILED"
        elif name.startswith("sceneworks.process."):
            process = dict(result.get("process") or {})
            payload = {**self._input_payload(name, args), **process}
            if name == "sceneworks.process.start":
                status = "RUNNING"
            elif name == "sceneworks.process.stop":
                status = "COMPLETED"
            elif process.get("running"):
                status = "RUNNING"
            else:
                returncode = process.get("returncode")
                status = "COMPLETED" if returncode in {None, 0} else "FAILED"
        return category, operation, status, payload
