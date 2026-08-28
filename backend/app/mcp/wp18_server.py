"""WP18 controlled managed-PCS GUI automation MCP extension."""

from __future__ import annotations

import uuid
from typing import Any

from app.mcp.server import MCPToolError, _bounded, _tool
from app.mcp.wp17_server import GuiEvidenceMCPServer
from app.services.gui_automation import GuiAutomationServiceError


_WP18_RICH_IMAGE_TOOLS = {
    "sceneworks.pcs.gui.invoke",
    "sceneworks.pcs.gui.set_value",
    "sceneworks.pcs.gui.select",
    "sceneworks.pcs.gui.toggle",
}


class GuiAutomationMCPServer(GuiEvidenceMCPServer):
    """Accessibility-control automation restricted to the managed PCS process."""

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
        if self.mode != "advanced":
            return tools
        read_only = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        }
        gui_action = {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        }
        session = {"type": "integer", "minimum": 1}
        turn = {
            "type": "string",
            "minLength": 1,
            "maxLength": 40,
            "description": "Optional active EngineeringTurn id for causal evidence correlation.",
        }
        control = {
            "type": "string",
            "minLength": 5,
            "maxLength": 1600,
            "description": "Opaque control_id returned by sceneworks.pcs.controls. It is bound to one managed PCS window and may become stale when the UI rebuilds.",
        }
        settle = {
            "type": "integer",
            "minimum": 0,
            "maximum": 2000,
            "default": 150,
            "description": "Milliseconds to allow the PCS UI to settle before SceneWorks captures after-action evidence.",
        }
        tools.extend(
            [
                _tool(
                    "sceneworks.pcs.controls",
                    "Enumerate Windows UI Automation controls under one visible managed PCS window. Returns opaque control ids, accessibility names/types/patterns and bounds; it does not expose arbitrary desktop controls.",
                    {
                        "session_id": session,
                        "window_id": {"type": "string", "maxLength": 80},
                        "max_controls": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000,
                            "default": 500,
                        },
                        "turn_id": turn,
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.gui.invoke",
                    "Invoke a managed PCS accessibility control through the UIA Invoke pattern. SceneWorks captures mandatory before/after screenshots and deterministic visual comparison evidence. No coordinate click is used.",
                    {
                        "session_id": session,
                        "control_id": control,
                        "settle_ms": settle,
                        "turn_id": turn,
                    },
                    gui_action,
                    required=["session_id", "control_id"],
                ),
                _tool(
                    "sceneworks.pcs.gui.set_value",
                    "Set a managed PCS accessibility control through the UIA Value pattern. The typed value is not persisted in evidence; only length and SHA-256 are recorded. Before/after visual evidence is mandatory.",
                    {
                        "session_id": session,
                        "control_id": control,
                        "value": {"type": "string", "maxLength": 16000},
                        "settle_ms": settle,
                        "turn_id": turn,
                    },
                    gui_action,
                    required=["session_id", "control_id", "value"],
                ),
                _tool(
                    "sceneworks.pcs.gui.select",
                    "Select a managed PCS accessibility item through the UIA SelectionItem pattern with mandatory before/after visual evidence.",
                    {
                        "session_id": session,
                        "control_id": control,
                        "settle_ms": settle,
                        "turn_id": turn,
                    },
                    gui_action,
                    required=["session_id", "control_id"],
                ),
                _tool(
                    "sceneworks.pcs.gui.toggle",
                    "Toggle a managed PCS accessibility control through the UIA Toggle pattern with mandatory before/after visual evidence.",
                    {
                        "session_id": session,
                        "control_id": control,
                        "settle_ms": settle,
                        "turn_id": turn,
                    },
                    gui_action,
                    required=["session_id", "control_id"],
                ),
            ]
        )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        args = arguments or {}
        handlers = {
            "sceneworks.pcs.controls": self._pcs_controls,
            "sceneworks.pcs.gui.invoke": self._gui_invoke,
            "sceneworks.pcs.gui.set_value": self._gui_set_value,
            "sceneworks.pcs.gui.select": self._gui_select,
            "sceneworks.pcs.gui.toggle": self._gui_toggle,
        }
        handler = handlers.get(name)
        if handler is None:
            return await super().call_tool(name, args)
        self._require_advanced()
        try:
            result = await handler(args)
            if name in _WP18_RICH_IMAGE_TOOLS:
                return result
            return _bounded(result, int(self.ctx.settings.mcp_tool_max_chars))
        except (GuiAutomationServiceError, ValueError, TypeError) as exc:
            raise MCPToolError(str(exc)) from exc

    async def _pcs_controls(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_automation.controls(
            int(args.get("session_id")),
            window_id=str(args.get("window_id") or "").strip() or None,
            max_controls=int(args.get("max_controls") or 500),
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _gui_invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_automation.invoke(
            int(args.get("session_id")),
            str(args.get("control_id") or ""),
            settle_ms=int(args.get("settle_ms") or 0) if args.get("settle_ms") is not None else 150,
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _gui_set_value(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_automation.set_value(
            int(args.get("session_id")),
            str(args.get("control_id") or ""),
            str(args.get("value") or ""),
            settle_ms=int(args.get("settle_ms") or 0) if args.get("settle_ms") is not None else 150,
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _gui_select(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_automation.select(
            int(args.get("session_id")),
            str(args.get("control_id") or ""),
            settle_ms=int(args.get("settle_ms") or 0) if args.get("settle_ms") is not None else 150,
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _gui_toggle(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.gui_automation.toggle(
            int(args.get("session_id")),
            str(args.get("control_id") or ""),
            settle_ms=int(args.get("settle_ms") or 0) if args.get("settle_ms") is not None else 150,
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def handle(self, payload: Any) -> tuple[Any | None, int]:
        """Extend WP17 rich image handling to GUI mutations, including batches."""
        if isinstance(payload, list):
            if not payload:
                return await super().handle(payload)
            replies: list[dict[str, Any]] = []
            for item in payload:
                if (
                    isinstance(item, dict)
                    and item.get("method") == "tools/call"
                    and isinstance(item.get("params") or {}, dict)
                    and str((item.get("params") or {}).get("name") or "")
                    in _WP18_RICH_IMAGE_TOOLS
                ):
                    replies.append(await self._handle_rich_call(item))
                    continue
                body, _status = await super().handle(item)
                if body is not None:
                    replies.append(body)
            return (replies if replies else None), (200 if replies else 202)

        if isinstance(payload, dict) and payload.get("method") == "tools/call":
            params = payload.get("params") or {}
            if (
                isinstance(params, dict)
                and str(params.get("name") or "") in _WP18_RICH_IMAGE_TOOLS
            ):
                return await self._handle_rich_call(payload), 200
        return await super().handle(payload)
