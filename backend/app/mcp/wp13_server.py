"""WP13 work-management extension for the SceneWorks MCP semantic surface."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.task_states import TaskStatus
from app.models import Initiative, Project, Task, WorkPackage
from app.mcp.attachments_server import AttachmentMCPServer
from app.mcp.server import MCPToolError, _bounded
from app.schemas import CapabilityProfile, EngineeringContract, TaskCreate


class WorkManagementMCPServer(AttachmentMCPServer):
    """Add lightweight backlog classification and execution intent to MCP tasks."""

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
        for tool in tools:
            if tool.get("name") != "sceneworks.create_task":
                continue
            properties = tool.get("inputSchema", {}).get("properties", {})
            properties["work_item_type"] = {
                "type": "string",
                "enum": ["task", "bug", "feature", "idea"],
                "default": "task",
                "description": "Backlog classification only; it does not grant execution authority.",
            }
            properties["requested_mode"] = {
                "type": "string",
                "enum": ["auto", "change", "investigate", "plan", "ask"],
                "default": "auto",
                "description": "Execution intent. Explicit read-only modes cannot be promoted to code changes by triage.",
            }
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        args = arguments or {}
        if name == "sceneworks.create_task":
            result = await self._create_wp13_task(args)
            return _bounded(result, int(self.ctx.settings.mcp_tool_max_chars))

        result = await super().call_tool(name, args)
        if name == "sceneworks.list_tasks":
            await self._enrich_task_rows(result.get("tasks") or [])
        elif name == "sceneworks.get_task":
            task = result.get("task")
            if isinstance(task, dict):
                await self._enrich_task_rows([task])
        elif name == "sceneworks.get_project_context":
            await self._enrich_task_rows(result.get("recent_tasks") or [])
        return result

    async def _enrich_task_rows(self, rows: list[dict[str, Any]]) -> None:
        ids = [int(row["id"]) for row in rows if isinstance(row, dict) and row.get("id")]
        if not ids:
            return
        async with self.ctx.engine_factory() as session:
            tasks = list(
                (
                    await session.execute(select(Task).where(Task.id.in_(ids)))
                ).scalars().all()
            )
        by_id = {task.id: task for task in tasks}
        for row in rows:
            task = by_id.get(int(row.get("id") or 0))
            if task is None:
                continue
            row.update(_work_management_fields(task))

    async def _create_wp13_task(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_standard()
        try:
            project_id = int(args.get("project_id"))
        except (TypeError, ValueError) as exc:
            raise MCPToolError("project_id is required") from exc
        try:
            body = TaskCreate(
                project_id=project_id,
                work_package_id=(
                    int(args["work_package_id"])
                    if args.get("work_package_id") is not None
                    else None
                ),
                title=str(args.get("title") or ""),
                description=str(args.get("description") or ""),
                priority=str(args.get("priority") or "medium"),
                work_item_type=str(args.get("work_item_type") or "task"),
                requested_mode=str(args.get("requested_mode") or "auto"),
                engineering_contract=EngineeringContract.model_validate(
                    args.get("engineering_contract") or {}
                ),
                capability_requirements=CapabilityProfile.model_validate(
                    args.get("capability_requirements") or {}
                ),
            )
        except Exception as exc:  # noqa: BLE001 - validation details are useful at the MCP boundary
            raise MCPToolError(f"invalid task: {exc}") from exc

        async with self.ctx.engine_factory() as session:
            project = await session.get(Project, body.project_id)
            if project is None:
                raise MCPToolError(f"project {body.project_id} not found")
            if body.work_package_id is not None:
                work_package = await session.get(WorkPackage, body.work_package_id)
                if work_package is None:
                    raise MCPToolError(f"work package {body.work_package_id} not found")
                initiative = await session.get(Initiative, work_package.initiative_id)
                if initiative is None or initiative.project_id != body.project_id:
                    raise MCPToolError("work package belongs to a different project")
            task = Task(
                project_id=body.project_id,
                work_package_id=body.work_package_id,
                title=body.title,
                description=body.description,
                priority=body.priority,
                work_item_type=body.work_item_type,
                requested_mode=body.requested_mode,
                resolved_mode=None if body.requested_mode == "auto" else body.requested_mode,
                engineering_contract=body.engineering_contract.model_dump(),
                capability_requirements=body.capability_requirements.model_dump(),
                status=TaskStatus.NEW.value,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
        return {
            "task": {
                "id": task.id,
                "project_id": task.project_id,
                "work_package_id": task.work_package_id,
                "title": task.title,
                "status": task.status,
                "priority": task.priority,
                **_work_management_fields(task),
            },
            "next": "Review the backlog item and context, then call sceneworks.task_action with start-architecture when ready to start work.",
        }


def _work_management_fields(task: Task) -> dict[str, Any]:
    return {
        "work_item_type": task.work_item_type,
        "requested_mode": task.requested_mode,
        "resolved_mode": task.resolved_mode,
        "effective_mode": task.resolved_mode or task.requested_mode,
    }
