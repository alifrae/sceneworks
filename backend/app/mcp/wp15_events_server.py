"""WP15 completion layer: correlated histories and task-facing evidence summaries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.engineering_models import EngineeringSession, EngineeringTurn
from app.models import Event
from app.mcp.server import MCPToolError, _bounded
from app.mcp.wp15_server import EvidenceMCPServer
from app.services.engineering_sessions import engineering_session_row


class CompleteEvidenceMCPServer(EvidenceMCPServer):
    """Expose one correlated history across direct actions and delegated agents."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        result = await super().call_tool(name, arguments)
        if name != "sceneworks.get_task":
            return result
        args = arguments or {}
        task_id = int(args.get("task_id"))
        async with self.ctx.engine_factory() as session:
            sessions = list(
                (
                    await session.execute(
                        select(EngineeringSession)
                        .where(EngineeringSession.task_id == task_id)
                        .order_by(EngineeringSession.created_at.desc())
                        .limit(20)
                    )
                ).scalars().all()
            )
        linked = []
        for row in sessions:
            linked.append(
                {
                    "session": engineering_session_row(row),
                    "evidence_summary": await self.ctx.engineering_evidence.summary(row.id),
                }
            )
        return _bounded(
            {**result, "engineering_sessions": linked},
            int(self.ctx.settings.mcp_tool_max_chars),
        )

    async def _engineering_session_close(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = int(args.get("session_id"))
        async with self.ctx.engine_factory() as session:
            active = (
                await session.execute(
                    select(EngineeringTurn)
                    .where(
                        EngineeringTurn.engineering_session_id == session_id,
                        EngineeringTurn.status == "ACTIVE",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        if active is not None:
            raise MCPToolError(
                f"engineering session {session_id} still has active turn {active.id}; "
                "finish the turn explicitly before closing the session"
            )
        return await super()._engineering_session_close(args)

    async def _events(self, args: dict[str, Any]) -> dict[str, Any]:
        result = await super()._events(args)
        links = {
            str(event.get("payload", {}).get("execution_id")): {
                "turn_id": event.get("turn_id"),
                "action_id": event.get("action_id"),
            }
            for event in result.get("events", [])
            if event.get("category") == "agent"
            and event.get("payload", {}).get("execution_id")
        }
        execution_ids = set(links)
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
                    "turn_id": links.get(str(row.execution_id), {}).get("turn_id"),
                    "action_id": links.get(str(row.execution_id), {}).get("action_id"),
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
