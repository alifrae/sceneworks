"""WP16 semantic PCS runtime-control MCP extension."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from app.mcp.server import MCPToolError, _bounded, _tool
from app.mcp.wp15_events_server import CompleteEvidenceMCPServer
from app.pcs_schemas import PcsRuntimeControlConfig
from app.services.engineering_evidence import EngineeringEvidenceError
from app.services.pcs_control import PcsControlError


class PcsRuntimeMCPServer(CompleteEvidenceMCPServer):
    """Expose PCS semantics while keeping raw OS/asset roots behind SceneWorks."""

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
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
        destructive = {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        }
        turn_property = {
            "type": "string",
            "minLength": 1,
            "maxLength": 40,
            "description": "Optional EngineeringTurn id for causal evidence correlation.",
        }

        tools.append(
            _tool(
                "sceneworks.pcs.get_config",
                "Get MCP-safe PCS run profiles, runbooks, expected health checks and configured asset aliases for a project. External host roots are never returned.",
                {"project_id": {"type": "integer", "minimum": 1}},
                read_only,
                required=["project_id"],
            )
        )
        if self.mode != "advanced":
            return tools

        session_property = {"type": "integer", "minimum": 1}
        profile_property = {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        }
        tools.extend(
            [
                _tool(
                    "sceneworks.pcs.configure",
                    "Replace a project's validated PCS runtime configuration: profiles, loopback API checks, verification runbooks and explicit read-only external asset roots.",
                    {
                        "project_id": {"type": "integer", "minimum": 1},
                        "config": {"type": "object"},
                    },
                    destructive,
                    required=["project_id", "config"],
                ),
                _tool(
                    "sceneworks.pcs.start",
                    "Start PCS using a configured run profile. SceneWorks owns the process and continuously captures stdout/stderr as evidence.",
                    {
                        "session_id": session_property,
                        "profile": profile_property,
                        "turn_id": turn_property,
                        "wait_for_health": {"type": "boolean", "default": True},
                    },
                    action,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.stop",
                    "Stop the SceneWorks-managed PCS process for an EngineeringSession and finalize process/log evidence.",
                    {"session_id": session_property, "turn_id": turn_property},
                    destructive,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.restart",
                    "Restart PCS deterministically, optionally selecting a different configured run profile.",
                    {
                        "session_id": session_property,
                        "profile": profile_property,
                        "turn_id": turn_property,
                    },
                    destructive,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.status",
                    "Get the current managed PCS run, PID, profile and exit/crash state.",
                    {"session_id": session_property, "turn_id": turn_property},
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.logs",
                    "Read structured durable PCS stdout/stderr evidence with severity/source/text filters and an evidence cursor.",
                    {
                        "session_id": session_property,
                        "run_id": {"type": "integer", "minimum": 1},
                        "severity": {
                            "type": "string",
                            "enum": ["debug", "info", "warning", "error", "critical"],
                        },
                        "source": {"type": "string", "enum": ["stdout", "stderr"]},
                        "contains": {"type": "string", "maxLength": 1000},
                        "after_id": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.errors",
                    "Read recent PCS error/critical log evidence without trusting an agent summary.",
                    {
                        "session_id": session_property,
                        "run_id": {"type": "integer", "minimum": 1},
                        "contains": {"type": "string", "maxLength": 1000},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.log_tail",
                    "Return the most recent structured PCS log events from durable SceneWorks evidence.",
                    {
                        "session_id": session_property,
                        "run_id": {"type": "integer", "minimum": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.health",
                    "Check managed process state plus configured TCP ports and optional loopback PCS API health endpoint.",
                    {
                        "session_id": session_property,
                        "run_id": {"type": "integer", "minimum": 1},
                        "turn_id": turn_property,
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.runtime_state",
                    "Read semantic PCS runtime state from the configured PCS API when available; otherwise return explicit unknowns plus process state.",
                    {
                        "session_id": session_property,
                        "run_id": {"type": "integer", "minimum": 1},
                        "turn_id": turn_property,
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.pcs.assets",
                    "List files under one explicit project-scoped read-only external asset alias. Host root paths are never exposed.",
                    {
                        "session_id": session_property,
                        "asset_root": {"type": "string", "minLength": 1, "maxLength": 120},
                        "path": {"type": "string", "default": "", "maxLength": 1000},
                        "recursive": {"type": "boolean", "default": False},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
                        "turn_id": turn_property,
                    },
                    read_only,
                    required=["session_id", "asset_root"],
                ),
                _tool(
                    "sceneworks.pcs.asset_info",
                    "Get size/mtime and optionally SHA-256 for one file under an explicit PCS asset alias.",
                    {
                        "session_id": session_property,
                        "asset_root": {"type": "string", "minLength": 1, "maxLength": 120},
                        "path": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "include_sha256": {"type": "boolean", "default": False},
                        "turn_id": turn_property,
                    },
                    read_only,
                    required=["session_id", "asset_root", "path"],
                ),
                _tool(
                    "sceneworks.pcs.run_verification",
                    "Execute a named deterministic PCS verification runbook and persist every step/result as SceneWorks evidence.",
                    {
                        "session_id": session_property,
                        "runbook": {"type": "string", "minLength": 1, "maxLength": 120},
                        "turn_id": turn_property,
                    },
                    action,
                    required=["session_id", "runbook"],
                ),
            ]
        )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        args = arguments or {}
        handlers = {
            "sceneworks.pcs.get_config": self._pcs_get_config,
            "sceneworks.pcs.configure": self._pcs_configure,
            "sceneworks.pcs.start": self._pcs_start,
            "sceneworks.pcs.stop": self._pcs_stop,
            "sceneworks.pcs.restart": self._pcs_restart,
            "sceneworks.pcs.status": self._pcs_status,
            "sceneworks.pcs.logs": self._pcs_logs,
            "sceneworks.pcs.errors": self._pcs_errors,
            "sceneworks.pcs.log_tail": self._pcs_log_tail,
            "sceneworks.pcs.health": self._pcs_health,
            "sceneworks.pcs.runtime_state": self._pcs_runtime_state,
            "sceneworks.pcs.assets": self._pcs_assets,
            "sceneworks.pcs.asset_info": self._pcs_asset_info,
            "sceneworks.pcs.run_verification": self._pcs_run_verification,
        }
        handler = handlers.get(name)
        if handler is None:
            return await super().call_tool(name, args)
        if name != "sceneworks.pcs.get_config":
            self._require_advanced()
        try:
            return _bounded(
                await handler(args), int(self.ctx.settings.mcp_tool_max_chars)
            )
        except (
            PcsControlError,
            EngineeringEvidenceError,
            ValidationError,
            ValueError,
            TypeError,
        ) as exc:
            raise MCPToolError(str(exc)) from exc

    async def _pcs_get_config(self, args: dict[str, Any]) -> dict[str, Any]:
        config = await self.ctx.pcs_control.get_config(int(args.get("project_id")))
        return {
            "project_id": int(args.get("project_id")),
            "config": self.ctx.pcs_control.public_config(config),
        }

    async def _pcs_configure(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = int(args.get("project_id"))
        config = PcsRuntimeControlConfig.model_validate(args.get("config") or {})
        saved = await self.ctx.pcs_control.set_config(project_id, config)
        return {
            "project_id": project_id,
            "config": self.ctx.pcs_control.public_config(saved),
        }

    async def _pcs_start(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.pcs_control.start(
            int(args.get("session_id")),
            profile_name=str(args.get("profile") or "").strip() or None,
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
            wait_for_health=bool(args.get("wait_for_health", True)),
        )

    async def _pcs_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.pcs_control.stop(
            int(args.get("session_id")),
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _pcs_restart(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.pcs_control.restart(
            int(args.get("session_id")),
            profile_name=str(args.get("profile") or "").strip() or None,
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _pcs_status(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = int(args.get("session_id"))
        turn_id = self._turn(args)
        result = await self.ctx.pcs_control.status(session_id)
        return await self._record_observation(
            session_id, "pcs.status", result, turn_id=turn_id
        )

    async def _pcs_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.pcs_control.logs(
            int(args.get("session_id")),
            run_id=(int(args["run_id"]) if args.get("run_id") is not None else None),
            severity=str(args.get("severity") or "").strip() or None,
            source=str(args.get("source") or "").strip() or None,
            contains=str(args.get("contains") or "").strip() or None,
            after_id=(int(args["after_id"]) if args.get("after_id") is not None else None),
            limit=int(args.get("limit") or 200),
        )

    async def _pcs_errors(self, args: dict[str, Any]) -> dict[str, Any]:
        result = await self.ctx.pcs_control.logs(
            int(args.get("session_id")),
            run_id=(int(args["run_id"]) if args.get("run_id") is not None else None),
            contains=str(args.get("contains") or "").strip() or None,
            limit=min(1000, max(100, int(args.get("limit") or 100) * 4)),
        )
        wanted = {"error", "critical"}
        filtered = [event for event in result["events"] if event.get("severity") in wanted]
        limit = int(args.get("limit") or 100)
        return {**result, "events": filtered[:limit], "truncated": len(filtered) > limit}

    async def _pcs_log_tail(self, args: dict[str, Any]) -> dict[str, Any]:
        result = await self.ctx.pcs_control.logs(
            int(args.get("session_id")),
            run_id=(int(args["run_id"]) if args.get("run_id") is not None else None),
            limit=1000,
        )
        limit = int(args.get("limit") or 50)
        return {**result, "events": result["events"][-limit:], "truncated": len(result["events"]) > limit}

    async def _pcs_health(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = int(args.get("session_id"))
        turn_id = self._turn(args)
        result = await self.ctx.pcs_control.health(
            session_id,
            run_id=(int(args["run_id"]) if args.get("run_id") is not None else None),
        )
        return await self._record_observation(
            session_id, "pcs.health", result, turn_id=turn_id
        )

    async def _pcs_runtime_state(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = int(args.get("session_id"))
        turn_id = self._turn(args)
        result = await self.ctx.pcs_control.runtime_state(
            session_id,
            run_id=(int(args["run_id"]) if args.get("run_id") is not None else None),
        )
        return await self._record_observation(
            session_id, "pcs.runtime_state", result, turn_id=turn_id
        )

    async def _pcs_assets(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = int(args.get("session_id"))
        turn_id = self._turn(args)
        result = await self.ctx.pcs_control.list_assets(
            session_id,
            str(args.get("asset_root") or ""),
            path=str(args.get("path") or ""),
            recursive=bool(args.get("recursive", False)),
            limit=int(args.get("limit") or 200),
        )
        await self.ctx.engineering_evidence.record(
            session_id,
            category="asset",
            operation="pcs.assets",
            status="COMPLETED",
            payload={
                "asset_root": result.get("asset_root"),
                "path": str(args.get("path") or ""),
                "result_count": len(result.get("entries") or []),
                "truncated": result.get("truncated"),
            },
            turn_id=turn_id,
            action_id=uuid.uuid4().hex,
        )
        return result

    async def _pcs_asset_info(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = int(args.get("session_id"))
        turn_id = self._turn(args)
        result = await self.ctx.pcs_control.asset_info(
            session_id,
            str(args.get("asset_root") or ""),
            str(args.get("path") or ""),
            include_sha256=bool(args.get("include_sha256", False)),
        )
        await self.ctx.engineering_evidence.record(
            session_id,
            category="asset",
            operation="pcs.asset_info",
            status="COMPLETED",
            payload=result,
            turn_id=turn_id,
            action_id=uuid.uuid4().hex,
        )
        return result

    async def _pcs_run_verification(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.pcs_control.run_verification(
            int(args.get("session_id")),
            str(args.get("runbook") or ""),
            turn_id=self._turn(args),
            action_id=uuid.uuid4().hex,
        )

    async def _record_observation(
        self,
        session_id: int,
        operation: str,
        result: dict[str, Any],
        *,
        turn_id: str | None,
    ) -> dict[str, Any]:
        action_id = uuid.uuid4().hex
        await self.ctx.engineering_evidence.record(
            session_id,
            category="pcs",
            operation=operation,
            status="COMPLETED",
            payload=result,
            turn_id=turn_id,
            action_id=action_id,
        )
        return {**result, "evidence_action_id": action_id, "turn_id": turn_id}

    @staticmethod
    def _turn(args: dict[str, Any]) -> str | None:
        return str(args.get("turn_id") or "").strip() or None
