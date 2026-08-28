"""WP17 managed PCS GUI observation and visual-evidence MCP extension."""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.mcp.server import MCPToolError, _bounded, _tool
from app.mcp.wp16_server import PcsRuntimeMCPServer
from app.services.gui_evidence import GuiEvidenceError


_RICH_IMAGE_TOOLS = {
    "sceneworks.pcs.screenshot",
    "sceneworks.pcs.gui_artifact",
    "sceneworks.pcs.visual_compare",
}


class GuiEvidenceMCPServer(PcsRuntimeMCPServer):
    """Observation-only GUI evidence restricted to the managed PCS process."""

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
        if self.mode != "advanced":
            return tools
        read_only = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        }
        action = {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        }
        session = {"type": "integer", "minimum": 1}
        turn = {
            "type": "string",
            "minLength": 1,
            "maxLength": 40,
            "description": "Optional EngineeringTurn id for causal evidence correlation.",
        }
        tools.extend(
            [
                _tool(
                    "sceneworks.pcs.windows",
                    "List visible top-level windows owned by the live SceneWorks-managed PCS PID. This is not arbitrary desktop/window discovery.",
                    {
                        "session_id": session,
                        "visible_only": {"type": "boolean", "default": True},
                        "turn_id": turn,
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.dialogs",
                    "List visible dialog/owned windows belonging to the live managed PCS PID.",
                    {
                        "session_id": session,
                        "visible_only": {"type": "boolean", "default": True},
                        "turn_id": turn,
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.screenshot",
                    "Capture one managed PCS window as durable PNG evidence. With no window_id SceneWorks selects the largest visible non-dialog PCS window.",
                    {
                        "session_id": session,
                        "window_id": {"type": "string", "maxLength": 80},
                        "label": {"type": "string", "maxLength": 200},
                        "turn_id": turn,
                    },
                    action,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.gui_artifacts",
                    "List persistent screenshot/diff artifact metadata for this EngineeringSession without returning image bytes.",
                    {
                        "session_id": session,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.gui_artifact",
                    "Retrieve one previously captured GUI artifact from this EngineeringSession and return it as MCP image content after hash verification.",
                    {
                        "session_id": session,
                        "artifact_id": {"type": "integer", "minimum": 1},
                    },
                    read_only,
                    required=["session_id", "artifact_id"],
                ),
                _tool(
                    "sceneworks.pcs.visual_compare",
                    "Compare two captured PCS screenshots deterministically. Returns exact dimension/pixel-change metrics and, when changed, a persisted pixel-difference image.",
                    {
                        "session_id": session,
                        "before_artifact_id": {"type": "integer", "minimum": 1},
                        "after_artifact_id": {"type": "integer", "minimum": 1},
                        "turn_id": turn,
                    },
                    action,
                    required=["session_id", "before_artifact_id", "after_artifact_id"],
                ),
            ]
        )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        args = arguments or {}
        handlers = {
            "sceneworks.pcs.windows": self._gui_windows,
            "sceneworks.pcs.dialogs": self._gui_dialogs,
            "sceneworks.pcs.screenshot": self._gui_screenshot,
            "sceneworks.pcs.gui_artifacts": self._gui_artifacts,
            "sceneworks.pcs.gui_artifact": self._gui_artifact,
            "sceneworks.pcs.visual_compare": self._visual_compare,
        }
        handler = handlers.get(name)
        if handler is None:
            return await super().call_tool(name, args)
        self._require_advanced()
        try:
            result = await handler(args)
            if name in _RICH_IMAGE_TOOLS:
                return result
            return _bounded(result, int(self.ctx.settings.mcp_tool_max_chars))
        except (GuiEvidenceError, ValueError, TypeError) as exc:
            raise MCPToolError(str(exc)) from exc

    async def _gui_windows(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_evidence.windows(
            int(args.get("session_id")),
            dialogs_only=False,
            visible_only=bool(args.get("visible_only", True)),
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _gui_dialogs(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_evidence.windows(
            int(args.get("session_id")),
            dialogs_only=True,
            visible_only=bool(args.get("visible_only", True)),
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _gui_screenshot(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_evidence.screenshot(
            int(args.get("session_id")),
            window_id=str(args.get("window_id") or "").strip() or None,
            label=str(args.get("label") or "").strip(),
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _gui_artifacts(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_evidence.artifacts(
            int(args.get("session_id")), int(args.get("limit") or 50)
        )

    async def _gui_artifact(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_evidence.artifact(
            int(args.get("session_id")), int(args.get("artifact_id"))
        )

    async def _visual_compare(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_evidence.compare(
            int(args.get("session_id")),
            int(args.get("before_artifact_id")),
            int(args.get("after_artifact_id")),
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def handle(self, payload: Any) -> tuple[Any | None, int]:
        """Return screenshot/diff bytes as MCP image content, not giant JSON text."""
        if isinstance(payload, dict) and payload.get("method") == "tools/call":
            params = payload.get("params") or {}
            if isinstance(params, dict) and str(params.get("name") or "") in _RICH_IMAGE_TOOLS:
                request_id = payload.get("id")
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": "tool arguments must be an object"},
                    }, 200
                try:
                    structured = await self.call_tool(name, arguments)
                    image_data = structured.pop("image_base64", None)
                    content: list[dict[str, Any]] = [
                        {
                            "type": "text",
                            "text": json.dumps(structured, ensure_ascii=False, default=str, indent=2),
                        }
                    ]
                    if image_data:
                        content.append(
                            {
                                "type": "image",
                                "data": image_data,
                                "mimeType": "image/png",
                            }
                        )
                    result = {
                        "resultType": "complete",
                        "content": content,
                        "structuredContent": structured,
                        "isError": False,
                    }
                except MCPToolError as exc:
                    result = {
                        "resultType": "complete",
                        "content": [{"type": "text", "text": str(exc)}],
                        "structuredContent": {"error": str(exc), "tool": name},
                        "isError": True,
                    }
                return {"jsonrpc": "2.0", "id": request_id, "result": result}, 200
        return await super().handle(payload)
