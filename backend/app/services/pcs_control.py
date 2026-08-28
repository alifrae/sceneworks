"""Semantic PCS runtime control over SceneWorks-owned execution primitives (WP16)."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engineering_models import EngineeringEvidence, EngineeringSession
from app.models import Project
from app.pcs_models import PcsProjectControl, PcsRun
from app.pcs_schemas import PcsRunProfile, PcsRuntimeControlConfig
from app.runtime.base import CommandRuntimeError, ProcessSnapshot, RuntimeErrorBase
from app.services.engineering_evidence import EngineeringEvidenceService
from app.services.engineering_sessions import EngineeringSessionError, EngineeringSessionService

_ACTIVE_RUN_STATUSES = {"STARTING", "RUNNING", "STOPPING"}
_FINAL_RUN_STATUSES = {"EXITED", "CRASHED", "FAILED", "LOST", "STOPPED"}
_ASSET_PATTERN = re.compile(r"\{\{asset:([A-Za-z0-9._-]+):([^{}]+)\}\}")
_LEVEL_PATTERN = re.compile(
    r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|FATAL)\b", re.IGNORECASE
)
_SENSITIVE_ENV_MARKERS = (
    "PASSWORD",
    "PASSWD",
    "TOKEN",
    "SECRET",
    "API_KEY",
    "PRIVATE_KEY",
)
_MAX_LOG_EVENTS_PER_EVIDENCE = 200
_MAX_CAPTURE_HASH_BYTES = 64 * 1024 * 1024


class PcsControlError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_run(row: PcsRun) -> dict[str, Any]:
    metadata = dict(row.metadata_json or {})
    return {
        "id": row.id,
        "project_id": row.project_id,
        "engineering_session_id": row.engineering_session_id,
        "task_id": row.task_id,
        "turn_id": row.turn_id,
        "start_action_id": row.start_action_id,
        "profile": row.profile_name,
        "pid": row.pid,
        "status": row.status,
        "exit_code": row.exit_code,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "command": metadata.get("command"),
        "args": list(metadata.get("args") or []),
        "cwd": metadata.get("cwd", ""),
        "asset_refs": list(metadata.get("asset_refs") or []),
        "lost_reason": metadata.get("lost_reason"),
    }


def _severity(stream: str, text: str) -> str:
    match = _LEVEL_PATTERN.search(text)
    if match:
        level = match.group(1).upper()
        if level in {"WARN", "WARNING"}:
            return "warning"
        if level in {"CRITICAL", "FATAL"}:
            return "critical"
        return level.lower()
    return "error" if stream == "stderr" else "info"


class PcsControlService:
    """PCS semantics layered on EngineeringSession + ExecutionRuntime + evidence."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engineering_sessions: EngineeringSessionService,
        evidence: EngineeringEvidenceService,
    ) -> None:
        self._session_factory = session_factory
        self._engineering_sessions = engineering_sessions
        self._evidence = evidence
        self._monitors: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # --------------------------------------------------------------- config

    async def get_config(self, project_id: int) -> PcsRuntimeControlConfig:
        async with self._session_factory() as session:
            if await session.get(Project, project_id) is None:
                raise PcsControlError(f"project {project_id} not found")
            row = await session.get(PcsProjectControl, project_id)
        if row is None:
            return PcsRuntimeControlConfig()
        try:
            return PcsRuntimeControlConfig.model_validate(row.config or {})
        except Exception as exc:  # noqa: BLE001
            raise PcsControlError(
                f"stored PCS runtime configuration is invalid: {exc}"
            ) from exc

    async def set_config(
        self, project_id: int, config: PcsRuntimeControlConfig | dict[str, Any]
    ) -> PcsRuntimeControlConfig:
        validated = (
            config
            if isinstance(config, PcsRuntimeControlConfig)
            else PcsRuntimeControlConfig.model_validate(config)
        )
        self._validate_config_security(validated)
        async with self._session_factory() as session:
            if await session.get(Project, project_id) is None:
                raise PcsControlError(f"project {project_id} not found")
            row = await session.get(PcsProjectControl, project_id)
            if row is None:
                row = PcsProjectControl(
                    project_id=project_id, config=validated.model_dump()
                )
                session.add(row)
            else:
                row.config = validated.model_dump()
                row.updated_at = _now()
            await session.commit()
        return validated

    def public_config(self, config: PcsRuntimeControlConfig) -> dict[str, Any]:
        """MCP-safe config view: aliases/semantics, never external host roots/secrets."""
        return {
            "default_profile": config.default_profile,
            "profiles": {
                name: {
                    "command": profile.command,
                    "args": list(profile.args),
                    "cwd": profile.cwd,
                    "environment_keys": sorted(profile.environment),
                    "expected_ports": [
                        item.model_dump() for item in profile.expected_ports
                    ],
                    "log_paths": list(profile.log_paths),
                    "crash_paths": list(profile.crash_paths),
                    "api_base_url": profile.api_base_url,
                    "health_path": profile.health_path,
                    "runtime_state_path": profile.runtime_state_path,
                    "startup_timeout_seconds": profile.startup_timeout_seconds,
                }
                for name, profile in config.profiles.items()
            },
            "runbooks": {
                name: runbook.model_dump() for name, runbook in config.runbooks.items()
            },
            "asset_roots": {
                name: {"configured": True, "read_only": root.read_only}
                for name, root in config.asset_roots.items()
            },
        }

    def _validate_config_security(self, config: PcsRuntimeControlConfig) -> None:
        for name, root in config.asset_roots.items():
            path = Path(root.path).expanduser().resolve()
            if not path.is_dir():
                raise PcsControlError(
                    f"asset root {name!r} does not exist or is not a directory on the SceneWorks host"
                )
        for name, profile in config.profiles.items():
            for key in profile.environment:
                upper = key.upper()
                if any(marker in upper for marker in _SENSITIVE_ENV_MARKERS):
                    raise PcsControlError(
                        f"profile {name!r} environment key {key!r} looks secret-bearing; "
                        "PCS runtime configuration is persisted and must not contain secrets"
                    )

    # ----------------------------------------------------------- permissions

    async def _session(
        self, session_id: int, permission: str | None = None
    ) -> EngineeringSession:
        row = await self._engineering_sessions.get(session_id)
        if row.status != "ACTIVE":
            raise PcsControlError(
                f"engineering session {session_id} is {row.status.lower()}, not active"
            )
        if permission and permission not in set(row.permissions or []):
            raise PcsControlError(
                f"engineering session {session_id} does not grant {permission}"
            )
        return row

    async def has_active_run(self, session_id: int) -> bool:
        return await self._latest_run(session_id, active_only=True) is not None

    async def _profile(
        self, engineering: EngineeringSession, profile_name: str | None
    ) -> tuple[PcsRuntimeControlConfig, str, PcsRunProfile]:
        config = await self.get_config(engineering.project_id)
        selected = (profile_name or config.default_profile or "").strip()
        if not selected:
            raise PcsControlError("PCS has no default run profile; specify profile")
        try:
            profile = config.profiles[selected]
        except KeyError as exc:
            raise PcsControlError(
                f"PCS run profile {selected!r} is not configured"
            ) from exc
        return config, selected, profile

    # --------------------------------------------------------------- assets

    def _asset_target(
        self,
        config: PcsRuntimeControlConfig,
        alias: str,
        relative: str,
        *,
        require_file: bool = True,
    ) -> tuple[Path, Path]:
        try:
            root_config = config.asset_roots[alias]
        except KeyError as exc:
            raise PcsControlError(f"unknown PCS asset root {alias!r}") from exc
        root = Path(root_config.path).expanduser().resolve()
        candidate = Path(relative or ".")
        if candidate.is_absolute():
            raise PcsControlError(
                "asset paths must be relative to their configured root"
            )
        target = (root / candidate).resolve()
        if not target.is_relative_to(root):
            raise PcsControlError("asset path escapes its configured root")
        if require_file and not target.is_file():
            raise PcsControlError(f"PCS asset not found: {alias}:{relative}")
        return root, target

    @staticmethod
    def _asset_descriptor(root: Path, target: Path) -> dict[str, Any]:
        stat = target.stat()
        return {
            "path": target.relative_to(root).as_posix(),
            "bytes": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }

    async def list_assets(
        self,
        session_id: int,
        alias: str,
        *,
        path: str = "",
        recursive: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        engineering = await self._session(session_id, "external_asset_read")
        config = await self.get_config(engineering.project_id)
        root, target = self._asset_target(config, alias, path, require_file=False)
        if not target.exists():
            raise PcsControlError(f"PCS asset path not found: {alias}:{path}")
        if target.is_file():
            items = [target]
        elif recursive:
            items = target.rglob("*")
        else:
            items = target.iterdir()
        rows: list[dict[str, Any]] = []
        bounded = max(1, min(int(limit), 1000))
        for item in items:
            if len(rows) >= bounded:
                break
            try:
                resolved = item.resolve()
                if not resolved.is_relative_to(root):
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            rows.append(
                {
                    "path": resolved.relative_to(root).as_posix(),
                    "type": "directory" if resolved.is_dir() else "file",
                    "bytes": stat.st_size if resolved.is_file() else None,
                    "modified_ns": stat.st_mtime_ns,
                }
            )
        rows.sort(key=lambda item: item["path"])
        return {
            "asset_root": alias,
            "entries": rows,
            "truncated": len(rows) >= bounded,
        }

    async def asset_info(
        self,
        session_id: int,
        alias: str,
        path: str,
        *,
        include_sha256: bool = False,
    ) -> dict[str, Any]:
        engineering = await self._session(session_id, "external_asset_read")
        config = await self.get_config(engineering.project_id)
        root, target = self._asset_target(config, alias, path)
        result = {"asset_root": alias, **self._asset_descriptor(root, target)}
        if include_sha256:
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            result["sha256"] = digest.hexdigest()
        return result

    def _expand_asset_args(
        self,
        config: PcsRuntimeControlConfig,
        args: list[str],
        *,
        allow_assets: bool,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        expanded: list[str] = []
        descriptors: list[dict[str, Any]] = []
        for raw in args:
            value = str(raw)
            matches = list(_ASSET_PATTERN.finditer(value))
            if matches and not allow_assets:
                raise PcsControlError(
                    "run profile/runbook references external assets but the EngineeringSession "
                    "does not grant external_asset_read"
                )
            for match in reversed(matches):
                alias, relative = match.group(1), match.group(2).strip()
                root, target = self._asset_target(config, alias, relative)
                descriptors.append(
                    {"asset_root": alias, **self._asset_descriptor(root, target)}
                )
                value = value[: match.start()] + str(target) + value[match.end() :]
            expanded.append(value)
        return expanded, descriptors

    # -------------------------------------------------------------- lifecycle

    async def _latest_run(
        self, session_id: int, *, active_only: bool = False
    ) -> PcsRun | None:
        async with self._session_factory() as session:
            query = select(PcsRun).where(
                PcsRun.engineering_session_id == session_id
            )
            if active_only:
                query = query.where(PcsRun.status.in_(_ACTIVE_RUN_STATUSES))
            query = query.order_by(PcsRun.id.desc()).limit(1)
            return (await session.execute(query)).scalar_one_or_none()

    async def start(
        self,
        session_id: int,
        *,
        profile_name: str | None = None,
        turn_id: str | None = None,
        action_id: str | None = None,
        wait_for_health: bool = True,
    ) -> dict[str, Any]:
        engineering, runtime, worktree = await self._engineering_sessions.runtime_for(
            session_id, "process_control"
        )
        if await self.has_active_run(session_id):
            raise PcsControlError(
                "PCS is already managed as running for this EngineeringSession; use pcs.restart or pcs.stop"
            )
        if turn_id:
            await self._evidence.validate_turn(session_id, turn_id)
        config, selected, profile = await self._profile(engineering, profile_name)
        allow_assets = "external_asset_read" in set(engineering.permissions or [])
        expanded_args, asset_refs = self._expand_asset_args(
            config, list(profile.args), allow_assets=allow_assets
        )
        action_id = action_id or uuid.uuid4().hex
        try:
            snapshot = await runtime.start_process(
                worktree,
                profile.command,
                expanded_args,
                cwd=profile.cwd,
                environment=profile.environment,
            )
        except RuntimeErrorBase as exc:
            await self._evidence.record(
                session_id,
                category="pcs",
                operation="pcs.start",
                status="FAILED",
                payload={
                    "profile": selected,
                    "command": profile.command,
                    "args": list(profile.args),
                    "cwd": profile.cwd,
                    "error": str(exc),
                    "asset_refs": asset_refs,
                },
                turn_id=turn_id,
                action_id=action_id,
            )
            raise PcsControlError(str(exc)) from exc

        try:
            async with self._session_factory() as session:
                row = PcsRun(
                    project_id=engineering.project_id,
                    engineering_session_id=session_id,
                    task_id=engineering.task_id,
                    turn_id=turn_id,
                    start_action_id=action_id,
                    profile_name=selected,
                    process_id=snapshot.process_id,
                    pid=snapshot.pid,
                    status="RUNNING",
                    output_cursor=0,
                    metadata_json={
                        "command": profile.command,
                        "args": list(profile.args),
                        "cwd": profile.cwd,
                        "asset_refs": asset_refs,
                    },
                    started_at=_now(),
                    updated_at=_now(),
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
                run_id = row.id
        except Exception:
            try:
                await runtime.stop_process(snapshot.process_id)
            except Exception:
                pass
            raise

        await self._evidence.record(
            session_id,
            category="pcs",
            operation="pcs.start",
            status="RUNNING",
            payload={
                "run_id": run_id,
                "profile": selected,
                "pid": snapshot.pid,
                "command": profile.command,
                "args": list(profile.args),
                "cwd": profile.cwd,
                "asset_refs": asset_refs,
            },
            turn_id=turn_id,
            action_id=action_id,
        )
        self._ensure_monitor(run_id)

        health = None
        if wait_for_health and (
            profile.expected_ports
            or (profile.api_base_url and profile.health_path)
        ):
            health = await self._wait_for_health(
                session_id, run_id, profile.startup_timeout_seconds
            )
        return {
            "run": await self.get_run(run_id),
            "health": health,
            "evidence_action_id": action_id,
        }

    async def stop(
        self,
        session_id: int,
        *,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        _, runtime, _ = await self._engineering_sessions.runtime_for(
            session_id, "process_control"
        )
        if turn_id:
            await self._evidence.validate_turn(session_id, turn_id)
        row = await self._latest_run(session_id, active_only=True)
        if row is None:
            return {"run": None, "already_stopped": True}
        action_id = action_id or uuid.uuid4().hex
        async with self._session_factory() as session:
            current = await session.get(PcsRun, row.id)
            if current:
                current.status = "STOPPING"
                current.updated_at = _now()
                await session.commit()
        try:
            snapshot = await runtime.stop_process(row.process_id)
        except RuntimeErrorBase as exc:
            await self._mark_lost(
                row.id, str(exc), turn_id=turn_id, action_id=action_id
            )
            raise PcsControlError(str(exc)) from exc
        await self._persist_snapshot_tail(row, snapshot)
        await self._finalize_snapshot(row.id, snapshot, stopped=True)
        await self._evidence.record(
            session_id,
            category="pcs",
            operation="pcs.stop",
            status="COMPLETED",
            payload={
                "run_id": row.id,
                "pid": snapshot.pid,
                "exit_code": snapshot.returncode,
            },
            turn_id=turn_id,
            action_id=action_id,
        )
        return {
            "run": await self.get_run(row.id),
            "evidence_action_id": action_id,
        }

    async def restart(
        self,
        session_id: int,
        *,
        profile_name: str | None = None,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        action_id = action_id or uuid.uuid4().hex
        previous = await self._latest_run(session_id, active_only=True)
        selected = profile_name or (previous.profile_name if previous else None)
        if previous is not None:
            await self.stop(session_id, turn_id=turn_id, action_id=action_id)
        result = await self.start(
            session_id,
            profile_name=selected,
            turn_id=turn_id,
            action_id=action_id,
        )
        result["restarted_from_run_id"] = previous.id if previous else None
        return result

    async def _run_model(self, run_id: int) -> PcsRun:
        async with self._session_factory() as session:
            row = await session.get(PcsRun, run_id)
            if row is None:
                raise PcsControlError(f"PCS run {run_id} not found")
            return row

    async def get_run(self, run_id: int) -> dict[str, Any]:
        return _public_run(await self._run_model(run_id))

    async def _refresh_run(self, row: PcsRun) -> PcsRun:
        if row.status not in _ACTIVE_RUN_STATUSES:
            return row
        try:
            _, runtime, _ = await self._engineering_sessions.runtime_for(
                row.engineering_session_id, "process_control"
            )
            snapshot = await runtime.process_output(
                row.process_id,
                cursor=row.output_cursor,
                max_events=_MAX_LOG_EVENTS_PER_EVIDENCE,
            )
            if snapshot.output:
                await self._persist_log_batch(row, snapshot.output, snapshot.next_cursor)
            if not snapshot.running and len(snapshot.output) < _MAX_LOG_EVENTS_PER_EVIDENCE:
                await self._finalize_snapshot(row.id, snapshot)
        except (EngineeringSessionError, RuntimeErrorBase) as exc:
            await self._mark_lost(row.id, str(exc))
        return await self._run_model(row.id)

    async def status(self, session_id: int) -> dict[str, Any]:
        await self._session(session_id, "process_control")
        row = await self._latest_run(session_id)
        if row is None:
            return {"managed": False, "run": None}
        row = await self._refresh_run(row)
        return {"managed": True, "run": _public_run(row)}

    # --------------------------------------------------------------- monitor

    def _ensure_monitor(self, run_id: int) -> None:
        current = self._monitors.get(run_id)
        if current is None or current.done():
            self._monitors[run_id] = asyncio.create_task(self._monitor(run_id))

    async def _monitor(self, run_id: int) -> None:
        try:
            while True:
                row = await self._run_model(run_id)
                if row.status not in _ACTIVE_RUN_STATUSES:
                    return
                try:
                    _, runtime, _ = await self._engineering_sessions.runtime_for(
                        row.engineering_session_id, "process_control"
                    )
                    snapshot = await runtime.process_output(
                        row.process_id,
                        cursor=row.output_cursor,
                        max_events=_MAX_LOG_EVENTS_PER_EVIDENCE,
                    )
                except (EngineeringSessionError, RuntimeErrorBase) as exc:
                    await self._mark_lost(run_id, str(exc))
                    return

                if snapshot.output:
                    await self._persist_log_batch(
                        row, snapshot.output, snapshot.next_cursor
                    )
                if not snapshot.running:
                    # Drain another batch when the final read filled the bound;
                    # otherwise a burst immediately before exit could be lost.
                    if len(snapshot.output) >= _MAX_LOG_EVENTS_PER_EVIDENCE:
                        continue
                    await self._finalize_snapshot(run_id, snapshot)
                    return
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        finally:
            self._monitors.pop(run_id, None)

    @staticmethod
    def _log_event(item: dict[str, Any]) -> dict[str, Any]:
        stream = str(item.get("stream") or "stdout")
        text = str(item.get("text") or "")
        return {
            "seq": item.get("seq"),
            "timestamp": item.get("timestamp") or _now().isoformat(),
            "severity": _severity(stream, text),
            "source": stream,
            "text": text,
        }

    async def _persist_log_batch(
        self, row: PcsRun, events: list[dict[str, Any]], next_cursor: int
    ) -> None:
        if not events:
            return
        await self._evidence.record(
            row.engineering_session_id,
            category="pcs_log",
            operation="pcs.log",
            status="RUNNING" if row.status in _ACTIVE_RUN_STATUSES else "COMPLETED",
            payload={
                "run_id": row.id,
                "pid": row.pid,
                "events": [self._log_event(item) for item in events],
            },
            turn_id=row.turn_id,
            action_id=row.start_action_id,
        )
        async with self._session_factory() as session:
            current = await session.get(PcsRun, row.id)
            if current:
                current.output_cursor = max(current.output_cursor, next_cursor)
                current.updated_at = _now()
                await session.commit()

    async def _persist_snapshot_tail(
        self, row: PcsRun, snapshot: ProcessSnapshot
    ) -> None:
        # stop_process returns the complete bounded native output buffer. Persist
        # only events the background monitor has not already committed.
        pending = [
            item
            for item in snapshot.output
            if int(item.get("seq", -1)) >= int(row.output_cursor)
        ]
        if not pending:
            return
        for offset in range(0, len(pending), _MAX_LOG_EVENTS_PER_EVIDENCE):
            chunk = pending[offset : offset + _MAX_LOG_EVENTS_PER_EVIDENCE]
            next_cursor = int(chunk[-1].get("seq", row.output_cursor)) + 1
            await self._persist_log_batch(row, chunk, next_cursor)
            row.output_cursor = next_cursor

    async def _finalize_snapshot(
        self, run_id: int, snapshot: ProcessSnapshot, *, stopped: bool = False
    ) -> None:
        async with self._lock:
            async with self._session_factory() as session:
                row = await session.get(PcsRun, run_id)
                if row is None or row.status in _FINAL_RUN_STATUSES:
                    return
                status = (
                    "STOPPED"
                    if stopped
                    else ("EXITED" if snapshot.returncode in {None, 0} else "CRASHED")
                )
                row.status = status
                row.exit_code = snapshot.returncode
                row.pid = snapshot.pid
                row.output_cursor = max(row.output_cursor, snapshot.next_cursor)
                row.finished_at = _now()
                row.updated_at = _now()
                session_id = row.engineering_session_id
                turn_id = row.turn_id
                action_id = row.start_action_id
                project_id = row.project_id
                profile_name = row.profile_name
                await session.commit()

            config = await self.get_config(project_id)
            profile = config.profiles.get(profile_name)
            artifacts: dict[str, Any] = {"logs": [], "crash": []}
            if profile:
                try:
                    _, _, worktree = await self._engineering_sessions.runtime_for(
                        session_id, "process_control"
                    )
                    artifacts = {
                        "logs": self._capture_paths(worktree, profile.log_paths),
                        "crash": self._capture_paths(worktree, profile.crash_paths),
                    }
                except EngineeringSessionError:
                    pass

            await self._evidence.record(
                session_id,
                category="pcs",
                operation="pcs.stopped" if stopped else "pcs.exit",
                status="FAILED" if status == "CRASHED" else "COMPLETED",
                payload={
                    "run_id": run_id,
                    "pid": snapshot.pid,
                    "exit_code": snapshot.returncode,
                    "state": status,
                    "artifacts": artifacts,
                },
                turn_id=turn_id,
                action_id=action_id,
            )

    @staticmethod
    def _capture_paths(worktree: Path, paths: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        root = worktree.resolve()
        for relative in paths:
            target = (root / relative).resolve()
            if not target.is_relative_to(root):
                continue
            if not target.is_file():
                rows.append({"path": relative, "exists": False})
                continue
            stat = target.stat()
            row: dict[str, Any] = {
                "path": target.relative_to(root).as_posix(),
                "exists": True,
                "bytes": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
            }
            if stat.st_size <= _MAX_CAPTURE_HASH_BYTES:
                row["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            else:
                row["sha256"] = None
                row["hash_skipped"] = "file exceeds 64 MiB evidence hash limit"
            rows.append(row)
        return rows

    async def _mark_lost(
        self,
        run_id: int,
        reason: str,
        *,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(PcsRun, run_id)
            if row is None or row.status in _FINAL_RUN_STATUSES:
                return
            row.status = "LOST"
            row.finished_at = _now()
            row.updated_at = _now()
            row.metadata_json = {
                **dict(row.metadata_json or {}),
                "lost_reason": reason,
            }
            session_id = row.engineering_session_id
            turn_id = turn_id or row.turn_id
            action_id = action_id or row.start_action_id
            await session.commit()
        await self._evidence.record(
            session_id,
            category="pcs",
            operation="pcs.lost",
            status="FAILED",
            payload={"run_id": run_id, "error": reason},
            turn_id=turn_id,
            action_id=action_id,
        )

    # --------------------------------------------------------------- logs

    async def logs(
        self,
        session_id: int,
        *,
        run_id: int | None = None,
        severity: str | None = None,
        source: str | None = None,
        contains: str | None = None,
        after_id: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        await self._session(session_id, "process_control")
        bounded = max(1, min(int(limit), 1000))
        async with self._session_factory() as session:
            query = select(EngineeringEvidence).where(
                EngineeringEvidence.engineering_session_id == session_id,
                EngineeringEvidence.category == "pcs_log",
            )
            if after_id is not None:
                query = query.where(EngineeringEvidence.id > after_id)
            rows = list(
                (
                    await session.execute(
                        query.order_by(EngineeringEvidence.id.asc()).limit(bounded * 4)
                    )
                ).scalars().all()
            )
        events: list[dict[str, Any]] = []
        latest_id = after_id
        for row in rows:
            latest_id = row.id
            payload = dict(row.payload or {})
            if run_id is not None and int(payload.get("run_id") or -1) != run_id:
                continue
            for event in payload.get("events") or []:
                if severity and str(event.get("severity")) != severity:
                    continue
                if source and str(event.get("source")) != source:
                    continue
                if contains and contains.lower() not in str(
                    event.get("text") or ""
                ).lower():
                    continue
                events.append({"evidence_id": row.id, **dict(event)})
                if len(events) >= bounded:
                    break
            if len(events) >= bounded:
                break
        return {
            "events": events,
            "next_after_id": latest_id,
            "truncated": len(events) >= bounded,
        }

    # --------------------------------------------------------------- health

    async def health(
        self, session_id: int, *, run_id: int | None = None
    ) -> dict[str, Any]:
        engineering = await self._session(session_id, "process_control")
        row = (
            await self._latest_run(session_id)
            if run_id is None
            else await self._run_model(run_id)
        )
        if row is None:
            return {
                "ready": False,
                "process": {"state": "not_started"},
                "checks": [],
            }
        if row.engineering_session_id != session_id:
            raise PcsControlError(
                "PCS run does not belong to this EngineeringSession"
            )
        _, _, profile = await self._profile(engineering, row.profile_name)
        row = await self._refresh_run(row)
        public_run = _public_run(row)
        process_running = row.status in _ACTIVE_RUN_STATUSES
        checks: list[dict[str, Any]] = []
        for check in profile.expected_ports:
            reachable = await self._port_open(check.host, check.port)
            checks.append(
                {
                    "type": "tcp_port",
                    "name": check.name or f"{check.host}:{check.port}",
                    "host": check.host,
                    "port": check.port,
                    "ok": reachable,
                }
            )
        if profile.api_base_url and profile.health_path:
            api = await self._api_get(profile, profile.health_path)
            checks.append(
                {"type": "pcs_api", "name": "PCS API health", **api}
            )
        ready = process_running and all(bool(item.get("ok")) for item in checks)
        if not checks:
            ready = process_running
        return {"ready": ready, "process": public_run, "checks": checks}

    async def _wait_for_health(
        self,
        session_id: int,
        run_id: int,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            last = await self.health(session_id, run_id=run_id)
            if last.get("ready"):
                return last
            process = last.get("process") or {}
            if process.get("status") in _FINAL_RUN_STATUSES:
                return last
            await asyncio.sleep(0.25)
        return {**last, "ready": False, "startup_timeout": True}

    @staticmethod
    async def _port_open(host: str, port: int) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    @staticmethod
    async def _api_get(profile: PcsRunProfile, path: str) -> dict[str, Any]:
        assert profile.api_base_url is not None
        url = urljoin(
            profile.api_base_url.rstrip("/") + "/", path.lstrip("/")
        )
        try:
            async with httpx.AsyncClient(
                timeout=2.0, follow_redirects=False
            ) as client:
                response = await client.get(url)
            content_type = response.headers.get("content-type", "")
            body: Any
            if "json" in content_type.lower():
                body = response.json()
            else:
                body = response.text[:4000]
            return {
                "ok": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "body": body,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # --------------------------------------------------------- runtime state

    async def runtime_state(
        self, session_id: int, *, run_id: int | None = None
    ) -> dict[str, Any]:
        engineering = await self._session(session_id, "process_control")
        row = (
            await self._latest_run(session_id)
            if run_id is None
            else await self._run_model(run_id)
        )
        if row is None:
            return {
                "source": "sceneworks_process",
                "pcs_api_available": False,
                "process": None,
                "state": {},
            }
        if row.engineering_session_id != session_id:
            raise PcsControlError(
                "PCS run does not belong to this EngineeringSession"
            )
        _, _, profile = await self._profile(engineering, row.profile_name)
        row = await self._refresh_run(row)
        process = _public_run(row)
        if profile.api_base_url and profile.runtime_state_path:
            api = await self._api_get(profile, profile.runtime_state_path)
            return {
                "source": "pcs_api",
                "pcs_api_available": bool(api.get("ok")),
                "process": process,
                "state": api.get("body") if api.get("ok") else {},
                "api": api,
            }
        return {
            "source": "sceneworks_process",
            "pcs_api_available": False,
            "process": process,
            "state": {
                "active_recording": None,
                "frame": None,
                "playback_state": None,
                "loaded_configuration": None,
                "active_views": None,
                "errors": None,
                "warnings": None,
            },
            "limitations": (
                "PCS semantic runtime state requires a configured PCS API "
                "runtime_state_path"
            ),
        }

    # -------------------------------------------------------------- runbooks

    async def run_verification(
        self,
        session_id: int,
        runbook_name: str,
        *,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        engineering = await self._session(session_id)
        if turn_id:
            await self._evidence.validate_turn(session_id, turn_id)
        config = await self.get_config(engineering.project_id)
        try:
            runbook = config.runbooks[runbook_name]
        except KeyError as exc:
            raise PcsControlError(
                f"PCS verification runbook {runbook_name!r} is not configured"
            ) from exc
        action_id = action_id or uuid.uuid4().hex
        results: list[dict[str, Any]] = []
        overall = True
        for index, step in enumerate(runbook.steps):
            try:
                result = await self._runbook_step(
                    session_id,
                    config,
                    index,
                    step.model_dump(),
                    turn_id=turn_id,
                    action_id=action_id,
                )
                ok = bool(result.get("ok", True))
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                ok = False
            results.append({"index": index, "action": step.action, **result})
            overall = overall and ok
            if not ok and runbook.stop_on_failure:
                break
        await self._evidence.record(
            session_id,
            category="verification",
            operation="pcs.run_verification",
            status="COMPLETED" if overall else "FAILED",
            payload={
                "runbook": runbook_name,
                "step_count": len(results),
                "passed": overall,
                "results": results,
            },
            turn_id=turn_id,
            action_id=action_id,
        )
        return {
            "runbook": runbook_name,
            "passed": overall,
            "steps": results,
            "evidence_action_id": action_id,
        }

    async def _runbook_step(
        self,
        session_id: int,
        config: PcsRuntimeControlConfig,
        index: int,
        step: dict[str, Any],
        *,
        turn_id: str | None,
        action_id: str,
    ) -> dict[str, Any]:
        action = step["action"]
        if action in {"start", "restart"}:
            result = (
                await self.start(
                    session_id,
                    profile_name=step.get("profile"),
                    turn_id=turn_id,
                    action_id=action_id,
                )
                if action == "start"
                else await self.restart(
                    session_id,
                    profile_name=step.get("profile"),
                    turn_id=turn_id,
                    action_id=action_id,
                )
            )
            health = result.get("health")
            ok = health is None or bool(health.get("ready"))
            return {
                "ok": ok,
                "run": result.get("run"),
                "health": health,
            }
        if action == "stop":
            result = await self.stop(
                session_id, turn_id=turn_id, action_id=action_id
            )
            return {"ok": True, "run": result.get("run")}
        if action == "health":
            result = await self.health(session_id)
            return {"ok": bool(result.get("ready")), "health": result}
        if action == "runtime_state":
            return {
                "ok": True,
                "runtime_state": await self.runtime_state(session_id),
            }
        if action == "command":
            engineering, runtime, worktree = (
                await self._engineering_sessions.runtime_for(
                    session_id, "shell_execute"
                )
            )
            profile = config.profiles.get(step.get("profile") or "")
            environment = profile.environment if profile else None
            cwd = str(step.get("cwd") or (profile.cwd if profile else ""))
            allow_assets = "external_asset_read" in set(
                engineering.permissions or []
            )
            args, asset_refs = self._expand_asset_args(
                config,
                list(step.get("args") or []),
                allow_assets=allow_assets,
            )
            started = _now()
            try:
                result = await runtime.run_command(
                    worktree,
                    str(step.get("command") or ""),
                    args,
                    cwd=cwd,
                    timeout=int(step.get("timeout_seconds") or 300),
                    environment=environment,
                )
                expected = step.get("expect_exit_code")
                ok = expected is None or result.returncode == expected
                payload = {
                    "step": index,
                    "command": step.get("command"),
                    "args": list(step.get("args") or []),
                    "cwd": cwd,
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "expected_exit_code": expected,
                    "asset_refs": asset_refs,
                }
                await self._evidence.record(
                    session_id,
                    category="verification",
                    operation="pcs.runbook.command",
                    status="COMPLETED" if ok else "FAILED",
                    payload=payload,
                    turn_id=turn_id,
                    action_id=action_id,
                    started_at=started,
                    finished_at=_now(),
                )
                return {"ok": ok, **payload}
            except CommandRuntimeError as exc:
                payload = {
                    "step": index,
                    "command": step.get("command"),
                    "args": list(step.get("args") or []),
                    "cwd": cwd,
                    **dict(exc.evidence or {}),
                    "error": str(exc),
                    "asset_refs": asset_refs,
                }
                await self._evidence.record(
                    session_id,
                    category="verification",
                    operation="pcs.runbook.command",
                    status="FAILED",
                    payload=payload,
                    turn_id=turn_id,
                    action_id=action_id,
                    started_at=started,
                    finished_at=_now(),
                )
                return {"ok": False, **payload}
        raise PcsControlError(f"unsupported runbook action: {action}")

    # -------------------------------------------------------------- recovery

    async def recover_interrupted(self) -> list[int]:
        recovered: list[tuple[int, int, str | None, str | None]] = []
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(PcsRun).where(
                            PcsRun.status.in_(_ACTIVE_RUN_STATUSES)
                        )
                    )
                ).scalars().all()
            )
            for row in rows:
                row.status = "LOST"
                row.finished_at = _now()
                row.updated_at = _now()
                row.metadata_json = {
                    **dict(row.metadata_json or {}),
                    "lost_reason": (
                        "SceneWorks restarted; native process handles are not recoverable"
                    ),
                }
                recovered.append(
                    (
                        row.id,
                        row.engineering_session_id,
                        row.turn_id,
                        row.start_action_id,
                    )
                )
            if rows:
                await session.commit()
        for run_id, session_id, turn_id, action_id in recovered:
            try:
                await self._evidence.record(
                    session_id,
                    category="pcs",
                    operation="pcs.lost",
                    status="FAILED",
                    payload={
                        "run_id": run_id,
                        "error": (
                            "SceneWorks restarted; native process handles are not recoverable"
                        ),
                    },
                    turn_id=turn_id,
                    action_id=action_id,
                )
            except Exception:
                # Recovery must not make application startup fail because an old
                # evidence correlation record is itself damaged.
                pass
        return [item[0] for item in recovered]

    async def shutdown(self) -> None:
        async with self._session_factory() as session:
            active = list(
                (
                    await session.execute(
                        select(PcsRun).where(
                            PcsRun.status.in_(_ACTIVE_RUN_STATUSES)
                        )
                    )
                ).scalars().all()
            )
        for row in active:
            try:
                await self.stop(row.engineering_session_id)
            except Exception:
                pass
        for task in list(self._monitors.values()):
            if not task.done():
                task.cancel()
        if self._monitors:
            await asyncio.gather(
                *self._monitors.values(), return_exceptions=True
            )
        self._monitors.clear()
