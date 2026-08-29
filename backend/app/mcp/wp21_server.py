"""WP21 task verification and explicit criterion mapping for SceneWorks MCP."""

from __future__ import annotations

from typing import Any

from app.mcp.server import MCPToolError, _bounded, _tool
from app.mcp.wp15_server import EvidenceMCPServer
from app.mcp.wp18_server import GuiAutomationMCPServer
from app.services.verification import TaskVerificationError


class VerificationMCPServer(GuiAutomationMCPServer):
    """Add objective task verification without creating a second evidence store."""

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
        read_only = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        }
        tools.append(
            _tool(
                "sceneworks.get_task_verification",
                "Project a task contract, Git provenance, project policy and durable SceneWorks evidence into PASS/FAIL/UNVERIFIABLE checks plus any accepted issue resolution snapshot.",
                {"task_id": {"type": "integer", "minimum": 1}},
                read_only,
                required=["task_id"],
            )
        )

        # Direct command evidence can be explicitly mapped to one or more
        # acceptance criteria. SceneWorks never guesses criterion mappings from
        # command text or agent prose.
        for tool in tools:
            if tool.get("name") != "sceneworks.command.run":
                continue
            properties = (tool.get("inputSchema") or {}).get("properties") or {}
            properties["criterion_ids"] = {
                "type": "array",
                "items": {"type": "string", "pattern": "^(?:AC)?[1-9][0-9]*$"},
                "maxItems": 50,
                "description": "Optional explicit acceptance-criterion ids (for example AC1). They are stored with objective command evidence; no semantic mapping is inferred by SceneWorks.",
            }
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if name == "sceneworks.get_task_verification":
            args = arguments or {}
            try:
                task_id = int(args.get("task_id"))
                return _bounded(
                    await self.ctx.verification.view(task_id),
                    int(self.ctx.settings.mcp_tool_max_chars),
                )
            except (TaskVerificationError, TypeError, ValueError) as exc:
                raise MCPToolError(str(exc)) from exc
        return await super().call_tool(name, arguments)

    @staticmethod
    def _input_payload(name: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = EvidenceMCPServer._input_payload(name, args)
        if name == "sceneworks.command.run" and isinstance(args.get("criterion_ids"), list):
            payload["criterion_ids"] = [str(item) for item in args["criterion_ids"][:50]]
        return payload
