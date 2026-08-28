"""WP15 durable evidence and turn-correlation extension for SceneWorks MCP."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.engineering_models import EngineeringSession
from app.models import Execution
from app.mcp.server import MCPToolError, _bounded, _tool
from app.mcp.wp14_server import ProviderNeutralMCPServer
from app.runtime.base import ProcessSnapshot, RuntimeErrorBase
from app.services.engineering_evidence import (
    EngineeringEvidenceError,
    evidence_row,
    turn_row,
)
from app.services.engineering_sessions import EngineeringSessionError, engineering_session_row


_INSTRUMENTED = {
    "sceneworks.engineering_session.create",
    "sceneworks.engineering_session.close",
    "sceneworks.workspace.list",
    "sceneworks.workspace.read",
    "sceneworks.workspace.search",
    "sceneworks.workspace.write",
    "sceneworks.command.run",
    "sceneworks.process.start",
    "sceneworks.process.output",
    "sceneworks.process.stop",
    "sceneworks.git.status",
    "sceneworks.git.diff",
    "sceneworks.git.commit",
    "sceneworks.agent.delegate",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceMCPServer(ProviderNeutralMCPServer):
    """Add task-bound turns and objective evidence to direct engineering control."""

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = super().tool_definitions()
        if self.mode != "advanced":
            return tools

        turn_property = {
            "type": "string",
            "minLength": 1,
            "maxLength": 40,
            "description": "Optional id returned by engineering_session.begin_turn. Reuse it to correlate actions from one supervisor iteration.",
        }
        for tool in tools:
            name = str(tool.get("name") or "")
            schema = tool.get("inputSchema") or {}
            properties = schema.get("properties") or {}
            if name == "sceneworks.engineering_session.create":
                properties["task_id"] = {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional governed task to bind to this EngineeringSession and all of its evidence.",
                }
            if name in _INSTRUMENTED and name not in {
                "sceneworks.engineering_session.create",
                "sceneworks.engineering_session.close",
            }:
                properties["turn_id"] = turn_property

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
        tools.extend(
            [
                _tool(
                    "sceneworks.engineering_session.begin_turn",
                    "Begin one explicit supervisor iteration so subsequent direct actions and delegated work can share a causal turn id.",
                    {
                        "session_id": {"type": "integer", "minimum": 1},
                        "intent": {"type": "string", "maxLength": 10000, "default": ""},
                    },
                    action,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.engineering_session.finish_turn",
                    "Close a supervisor turn after its evidence has been inspected.",
                    {
                        "session_id": {"type": "integer", "minimum": 1},
                        "turn_id": turn_property,
                        "status": {
                            "type": "string",
                            "enum": ["COMPLETED", "FAILED", "CANCELLED"],
                            "default": "COMPLETED",
                        },
                    },
                    action,
                    required=["session_id", "turn_id"],
                ),
                _tool(
                    "sceneworks.engineering_session.evidence",
                    "Retrieve durable SceneWorks-captured evidence for a direct EngineeringSession, optionally filtered by turn/category and cursor.",
                    {
                        "session_id": {"type": "integer", "minimum": 1},
                        "turn_id": turn_property,
                        "category": {"type": "string", "maxLength": 50},
                        "after_id": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.engineering_session.events",
                    "Get the correlated supervisor turns plus durable evidence stream for an EngineeringSession.",
                    {
                        "session_id": {"type": "integer", "minimum": 1},
                        "after_id": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    },
                    read_only,
                    required=["session_id"],
                ),
                _tool(
                    "sceneworks.engineering_session.summary",
                    "Return high-signal evidence counts, failures, task/base-commit correlation and latest evidence cursor without asking an agent to summarize itself.",
                    {"session_id": {"type": "integer", "minimum": 1}},
                    read_only,
                    required=["session_id"],
                ),
            ]
        )
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        args = arguments or {}
        own = {
            "sceneworks.engineering_session.begin_turn": self._begin_turn,
            "sceneworks.engineering_session.finish_turn": self._finish_turn,
            "sceneworks.engineering_session.evidence": self._evidence,
            "sceneworks.engineering_session.events": self._events,
            "sceneworks.engineering_session.summary": self._summary,
        }
        if handler := own.get(name):
            try:
                return _bounded(
                    await handler(args), int(self.ctx.settings.mcp_tool_max_chars)
                )
            except (EngineeringEvidenceError, EngineeringSessionError, ValueError, TypeError) as exc:
                raise MCPToolError(str(exc)) from exc

        if name not in _INSTRUMENTED:
            return await super().call_tool(name, args)

        started = _now()
        action_id = uuid.uuid4().hex
        turn_id = str(args.get("turn_id") or "").strip() or None
        session_id = self._session_id_from_args(args)
        if session_id is not None and turn_id:
            try:
                await self.ctx.engineering_evidence.validate_turn(session_id, turn_id)
            except EngineeringEvidenceError as exc:
                raise MCPToolError(str(exc)) from exc

        before = await self._pre_evidence(name, args, session_id)
        try:
            result = await super().call_tool(name, args)
        except Exception as exc:
            if session_id is not None:
                await self._record_failure(
                    session_id,
                    name,
                    args,
                    turn_id,
                    action_id,
                    started,
                    exc,
                    before,
                )
            raise

        if name == "sceneworks.engineering_session.create":
            session_id = int(result["session"]["id"])
        if session_id is not None:
            await self._record_success(
                session_id,
                name,
                args,
                result,
                turn_id,
                action_id,
                started,
                before,
            )
            result = {
                **result,
                "evidence_action_id": action_id,
                "turn_id": turn_id,
            }
        return result

    async def _engineering_session_create(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        backend = str(args.get("default_backend") or "").strip() or None
        if backend and backend not in self.ctx.backends.keys():
            raise MCPToolError(f"backend {backend!r} is not registered")
        row = await self.ctx.engineering_sessions.create(
            int(args.get("project_id")),
            task_id=(int(args["task_id"]) if args.get("task_id") is not None else None),
            permissions=(
                [str(item) for item in args.get("permissions", [])]
                if args.get("permissions") is not None
                else None
            ),
            runtime=str(args.get("runtime") or "native"),
            default_backend=backend,
            default_model=str(args.get("default_model") or "").strip() or None,
        )
        return {
            "session": engineering_session_row(row),
            "next": "Begin an engineering turn, then use direct workspace/git/command/process tools or delegate a worker.",
        }

    @staticmethod
    def _public_process(snapshot: ProcessSnapshot) -> dict[str, Any]:
        return {
            "process_id": snapshot.process_id,
            "pid": snapshot.pid,
            "command": snapshot.command,
            "started_at": snapshot.started_at,
            "finished_at": snapshot.finished_at,
            "running": snapshot.running,
            "returncode": snapshot.returncode,
            "next_cursor": snapshot.next_cursor,
            "output": snapshot.output,
        }

    async def _begin_turn(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        turn = await self.ctx.engineering_evidence.begin_turn(
            int(args.get("session_id")), str(args.get("intent") or "")
        )
        return {"turn": turn_row(turn)}

    async def _finish_turn(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        turn = await self.ctx.engineering_evidence.finish_turn(
            int(args.get("session_id")),
            str(args.get("turn_id") or ""),
            str(args.get("status") or "COMPLETED"),
        )
        return {"turn": turn_row(turn)}

    async def _evidence(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        rows = await self.ctx.engineering_evidence.list_evidence(
            int(args.get("session_id")),
            turn_id=str(args.get("turn_id") or "").strip() or None,
            category=str(args.get("category") or "").strip() or None,
            after_id=(int(args["after_id"]) if args.get("after_id") is not None else None),
            limit=int(args.get("limit") or 100),
        )
        return {
            "evidence": [evidence_row(row) for row in rows],
            "next_after_id": rows[-1].id if rows else args.get("after_id"),
        }

    async def _events(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        session_id = int(args.get("session_id"))
        rows = await self.ctx.engineering_evidence.list_evidence(
            session_id,
            after_id=(int(args["after_id"]) if args.get("after_id") is not None else None),
            limit=int(args.get("limit") or 100),
        )
        turns = await self.ctx.engineering_evidence.list_turns(session_id, limit=100)
        return {
            "turns": [turn_row(row) for row in turns],
            "events": [evidence_row(row) for row in rows],
            "next_after_id": rows[-1].id if rows else args.get("after_id"),
        }

    async def _summary(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_advanced()
        return await self.ctx.engineering_evidence.summary(int(args.get("session_id")))

    @staticmethod
    def _session_id_from_args(args: dict[str, Any]) -> int | None:
        value = args.get("session_id")
        if value is None:
            return None
        return int(value)

    async def _pre_evidence(
        self, name: str, args: dict[str, Any], session_id: int | None
    ) -> dict[str, Any]:
        if session_id is None or name != "sceneworks.workspace.write":
            return {}
        try:
            _, runtime, root = await self.ctx.engineering_sessions.runtime_for(
                session_id, "repository_write"
            )
            before = await runtime.read_text(
                root, str(args.get("path") or ""), max_chars=1
            )
            return {
                "sha256_before": before.get("sha256"),
                "bytes_before": None,
            }
        except (EngineeringSessionError, RuntimeErrorBase):
            return {"sha256_before": None, "bytes_before": None}

    async def _record_success(
        self,
        session_id: int,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        turn_id: str | None,
        action_id: str,
        started: datetime,
        before: dict[str, Any],
    ) -> None:
        category, operation, status, payload = self._success_payload(
            name, args, result, before
        )
        await self.ctx.engineering_evidence.record(
            session_id,
            category=category,
            operation=operation,
            status=status,
            payload=payload,
            turn_id=turn_id,
            action_id=action_id,
            started_at=started,
            finished_at=_now(),
        )
        if name == "sceneworks.agent.delegate":
            execution_id = str(result.get("execution_id") or "")
            if execution_id:
                async with self.ctx.engine_factory() as session:
                    execution = await session.get(Execution, execution_id)
                    if execution is not None:
                        execution.workspace = {
                            **dict(execution.workspace or {}),
                            "engineering_turn_id": turn_id,
                            "engineering_evidence_action_id": action_id,
                        }
                        await session.commit()

    async def _record_failure(
        self,
        session_id: int,
        name: str,
        args: dict[str, Any],
        turn_id: str | None,
        action_id: str,
        started: datetime,
        exc: Exception,
        before: dict[str, Any],
    ) -> None:
        category = self._category(name)
        payload = {
            **self._input_payload(name, args),
            **before,
            "error": f"{type(exc).__name__}: {exc}",
            "timed_out": "timed out" in str(exc).lower(),
            "cancelled": "cancel" in str(exc).lower(),
        }
        try:
            await self.ctx.engineering_evidence.record(
                session_id,
                category=category,
                operation=name.removeprefix("sceneworks."),
                status="FAILED",
                payload=payload,
                turn_id=turn_id,
                action_id=action_id,
                started_at=started,
                finished_at=_now(),
            )
        except EngineeringEvidenceError:
            # Evidence persistence must not hide the original engineering error.
            return

    def _success_payload(
        self,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        before: dict[str, Any],
    ) -> tuple[str, str, str, dict[str, Any]]:
        operation = name.removeprefix("sceneworks.")
        if name == "sceneworks.workspace.read":
            return "file", operation, "COMPLETED", {
                "path": result.get("path"),
                "sha256": result.get("sha256"),
                "start_line": result.get("start_line"),
                "end_line": result.get("end_line"),
                "total_lines": result.get("total_lines"),
                "truncated": result.get("truncated"),
            }
        if name == "sceneworks.workspace.write":
            return "file", operation, "COMPLETED", {
                "path": result.get("path"),
                **before,
                "sha256_after": result.get("sha256"),
                "bytes_after": result.get("bytes"),
                "created": result.get("created"),
            }
        if name in {"sceneworks.workspace.list", "sceneworks.workspace.search"}:
            return "file", operation, "COMPLETED", {
                **self._input_payload(name, args),
                "result_count": len(result.get("entries") or result.get("matches") or []),
                "truncated": result.get("truncated"),
            }
        if name == "sceneworks.command.run":
            return "command", operation, "COMPLETED", {
                **self._input_payload(name, args),
                "exit_code": result.get("returncode"),
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
                "timed_out": False,
                "cancelled": False,
            }
        if name.startswith("sceneworks.process."):
            process = dict(result.get("process") or {})
            return "process", operation, (
                "RUNNING" if process.get("running") else "COMPLETED"
            ), process
        if name.startswith("sceneworks.git."):
            return "git", operation, "COMPLETED", dict(result)
        if name == "sceneworks.agent.delegate":
            return "agent", operation, "STARTED", {
                "execution_id": result.get("execution_id"),
                "backend": result.get("backend"),
                "model": result.get("model"),
            }
        if name.startswith("sceneworks.engineering_session."):
            return "session", operation, "COMPLETED", {
                "session": result.get("session")
            }
        return self._category(name), operation, "COMPLETED", dict(result)

    @staticmethod
    def _input_payload(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "sceneworks.command.run":
            return {
                "command": args.get("command"),
                "arguments": list(args.get("args") or []),
                "cwd": args.get("cwd") or "",
                "timeout_seconds": int(args.get("timeout") or 300),
            }
        if name == "sceneworks.process.start":
            return {
                "command": args.get("command"),
                "arguments": list(args.get("args") or []),
                "cwd": args.get("cwd") or "",
            }
        if name == "sceneworks.process.output":
            return {
                "process_id": args.get("process_id"),
                "cursor": int(args.get("cursor") or 0),
            }
        if name == "sceneworks.process.stop":
            return {"process_id": args.get("process_id")}
        if name == "sceneworks.workspace.search":
            return {"query": args.get("query"), "path": args.get("path") or ""}
        if name == "sceneworks.workspace.list":
            return {"path": args.get("path") or "", "recursive": bool(args.get("recursive", False))}
        if name == "sceneworks.workspace.write":
            return {"path": args.get("path"), "create_only": bool(args.get("create_only", False))}
        return {}

    @staticmethod
    def _category(name: str) -> str:
        if ".workspace." in name:
            return "file"
        if ".command." in name:
            return "command"
        if ".process." in name:
            return "process"
        if ".git." in name:
            return "git"
        if ".agent." in name:
            return "agent"
        return "session"
