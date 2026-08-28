"""WP17 GUI observation and visual-evidence service.

The service is deliberately scoped to the OS PID of a SceneWorks-managed PCS
run. It persists screenshot bytes outside Git worktrees while the WP15 evidence
ledger stores hashes, dimensions, causal ids and storage references.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings, resolve_path
from app.engineering_models import EngineeringEvidence, EngineeringSession
from app.gui import GuiCapture, GuiObservationError, GuiObservationProvider, GuiWindow, SystemGuiObservationProvider
from app.gui.png import PngError, decode_rgb, encode_rgb
from app.services.attachments import read_bytes
from app.services.engineering_evidence import EngineeringEvidenceService
from app.services.engineering_sessions import EngineeringSessionService
from app.services.pcs_control import PcsControlService


class GuiEvidenceError(RuntimeError):
    pass


class GuiEvidenceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engineering_sessions: EngineeringSessionService,
        engineering_evidence: EngineeringEvidenceService,
        pcs_control: PcsControlService,
        settings: Settings,
        provider: GuiObservationProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engineering_sessions = engineering_sessions
        self._evidence = engineering_evidence
        self._pcs = pcs_control
        self._settings = settings
        self._provider: GuiObservationProvider = provider or SystemGuiObservationProvider()

    async def _session(self, session_id: int) -> EngineeringSession:
        row = await self._engineering_sessions.get(session_id)
        if row.status != "ACTIVE":
            raise GuiEvidenceError(
                f"engineering session {session_id} is {row.status.lower()}, not active"
            )
        if "gui_observe" not in set(row.permissions or []):
            raise GuiEvidenceError(
                f"engineering session {session_id} does not grant gui_observe"
            )
        return row

    async def _managed_pid(self, session_id: int) -> tuple[EngineeringSession, int, int]:
        row = await self._session(session_id)
        status = await self._pcs.status(session_id)
        run = status.get("run") or {}
        pid = int(run.get("pid") or 0)
        run_id = int(run.get("id") or 0)
        run_status = str(run.get("status") or "")
        if not pid or not run_id or run_status not in {"STARTING", "RUNNING", "STOPPING"}:
            raise GuiEvidenceError(
                "GUI observation requires a live SceneWorks-managed PCS run in this EngineeringSession"
            )
        return row, pid, run_id

    @staticmethod
    def _window_dict(window: GuiWindow) -> dict[str, Any]:
        return {
            "window_id": window.window_id,
            "title": window.title,
            "class_name": window.class_name,
            "visible": window.visible,
            "enabled": window.enabled,
            "rect": {
                "left": window.left,
                "top": window.top,
                "right": window.right,
                "bottom": window.bottom,
                "width": window.width,
                "height": window.height,
            },
            "is_dialog": window.is_dialog,
            "owner_window_id": window.owner_window_id,
        }

    async def windows(
        self,
        session_id: int,
        *,
        dialogs_only: bool = False,
        visible_only: bool = True,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        _row, pid, run_id = await self._managed_pid(session_id)
        try:
            windows = self._provider.list_windows(pid)
        except GuiObservationError as exc:
            raise GuiEvidenceError(str(exc)) from exc
        if visible_only:
            windows = [window for window in windows if window.visible]
        if dialogs_only:
            windows = [window for window in windows if window.is_dialog]
        public = [self._window_dict(window) for window in windows]
        evidence = await self._evidence.record(
            session_id,
            category="gui",
            operation="pcs.dialogs" if dialogs_only else "pcs.windows",
            status="COMPLETED",
            payload={
                "run_id": run_id,
                "window_count": len(public),
                "visible_only": visible_only,
                "dialogs_only": dialogs_only,
                "windows": public,
            },
            turn_id=turn_id,
            action_id=action_id or uuid.uuid4().hex,
        )
        return {
            "session_id": session_id,
            "run_id": run_id,
            "windows": public,
            "evidence_id": evidence.id,
            "evidence_action_id": evidence.action_id,
            "turn_id": turn_id,
        }

    async def screenshot(
        self,
        session_id: int,
        *,
        window_id: str | None = None,
        label: str = "",
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        row, pid, run_id = await self._managed_pid(session_id)
        try:
            windows = self._provider.list_windows(pid)
        except GuiObservationError as exc:
            raise GuiEvidenceError(str(exc)) from exc
        visible = [window for window in windows if window.visible and window.width > 0 and window.height > 0]
        if window_id:
            selected = next((window for window in visible if window.window_id == window_id), None)
            if selected is None:
                raise GuiEvidenceError(
                    "window_id is not a currently visible window owned by the managed PCS process"
                )
        else:
            candidates = [window for window in visible if not window.is_dialog] or visible
            if not candidates:
                raise GuiEvidenceError("managed PCS process has no visible capturable windows")
            selected = max(candidates, key=lambda item: item.width * item.height)

        try:
            capture = self._provider.capture_window(selected)
        except GuiObservationError as exc:
            raise GuiEvidenceError(str(exc)) from exc
        return await self._persist_capture(
            row,
            run_id,
            selected,
            capture,
            operation="pcs.screenshot",
            label=label,
            turn_id=turn_id,
            action_id=action_id or uuid.uuid4().hex,
            extra_payload={},
        )

    async def _persist_capture(
        self,
        engineering: EngineeringSession,
        run_id: int,
        window: GuiWindow | None,
        capture: GuiCapture,
        *,
        operation: str,
        label: str,
        turn_id: str | None,
        action_id: str,
        extra_payload: dict[str, Any],
    ) -> dict[str, Any]:
        data = capture.data
        if not data:
            raise GuiEvidenceError("captured screenshot is empty")
        if len(data) > self._settings.gui_screenshot_max_bytes:
            raise GuiEvidenceError(
                f"screenshot exceeds {self._settings.gui_screenshot_max_bytes} byte evidence limit"
            )
        digest = hashlib.sha256(data).hexdigest()
        root = resolve_path(self._settings.attachment_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        relative = (
            Path(str(engineering.project_id))
            / "_gui"
            / str(engineering.id)
            / f"{uuid.uuid4().hex}.png"
        )
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise GuiEvidenceError("GUI evidence storage path escapes SceneWorks storage root")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".png.tmp")
        temporary.write_bytes(data)
        temporary.replace(target)

        payload: dict[str, Any] = {
            "artifact_kind": "screenshot" if operation == "pcs.screenshot" else "visual_diff",
            "storage_key": relative.as_posix(),
            "sha256": digest,
            "size_bytes": len(data),
            "mime_type": capture.mime_type,
            "width": capture.width,
            "height": capture.height,
            "capture_method": capture.capture_method,
            "occlusion_safe": capture.occlusion_safe,
            "run_id": run_id,
            "label": label[:200],
            **extra_payload,
        }
        if window is not None:
            payload["window"] = self._window_dict(window)
        evidence = await self._evidence.record(
            engineering.id,
            category="gui",
            operation=operation,
            status="COMPLETED",
            payload=payload,
            turn_id=turn_id,
            action_id=action_id,
        )
        public = self._artifact_metadata(evidence)
        return {
            **public,
            "image_base64": base64.b64encode(data).decode("ascii"),
        }

    @staticmethod
    def _artifact_metadata(evidence: EngineeringEvidence) -> dict[str, Any]:
        payload = dict(evidence.payload or {})
        payload.pop("storage_key", None)
        return {
            "artifact_id": evidence.id,
            "session_id": evidence.engineering_session_id,
            "task_id": evidence.task_id,
            "turn_id": evidence.turn_id,
            "evidence_action_id": evidence.action_id,
            "operation": evidence.operation,
            "created_at": evidence.created_at.isoformat() if evidence.created_at else None,
            **payload,
        }

    async def _artifact_row(self, session_id: int, artifact_id: int) -> EngineeringEvidence:
        await self._session(session_id)
        async with self._session_factory() as session:
            row = await session.get(EngineeringEvidence, artifact_id)
            if (
                row is None
                or row.engineering_session_id != session_id
                or row.category != "gui"
                or not (row.payload or {}).get("storage_key")
            ):
                raise GuiEvidenceError("GUI artifact not found in this EngineeringSession")
            return row

    async def artifact(self, session_id: int, artifact_id: int) -> dict[str, Any]:
        row = await self._artifact_row(session_id, artifact_id)
        storage_key = str((row.payload or {}).get("storage_key") or "")
        try:
            data = read_bytes(self._settings, storage_key)
        except Exception as exc:
            raise GuiEvidenceError(f"GUI artifact content is unavailable: {exc}") from exc
        expected = str((row.payload or {}).get("sha256") or "")
        actual = hashlib.sha256(data).hexdigest()
        if expected and actual != expected:
            raise GuiEvidenceError("GUI artifact hash does not match persisted evidence")
        return {
            **self._artifact_metadata(row),
            "image_base64": base64.b64encode(data).decode("ascii"),
        }

    async def artifacts(self, session_id: int, limit: int = 50) -> dict[str, Any]:
        await self._session(session_id)
        rows = await self._evidence.list_evidence(
            session_id, category="gui", limit=max(1, min(limit, 200))
        )
        artifacts = [
            self._artifact_metadata(row)
            for row in rows
            if (row.payload or {}).get("storage_key")
        ]
        return {"session_id": session_id, "artifacts": artifacts}

    async def compare(
        self,
        session_id: int,
        before_artifact_id: int,
        after_artifact_id: int,
        *,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        engineering, _pid, run_id = await self._managed_pid(session_id)
        before_row = await self._artifact_row(session_id, before_artifact_id)
        after_row = await self._artifact_row(session_id, after_artifact_id)
        before_bytes = read_bytes(self._settings, str((before_row.payload or {})["storage_key"]))
        after_bytes = read_bytes(self._settings, str((after_row.payload or {})["storage_key"]))
        try:
            before_width, before_height, before_rgb = decode_rgb(before_bytes)
            after_width, after_height, after_rgb = decode_rgb(after_bytes)
        except PngError as exc:
            raise GuiEvidenceError(f"GUI artifact cannot be compared: {exc}") from exc

        same_dimensions = (before_width, before_height) == (after_width, after_height)
        changed_ratio: float | None = None
        changed_bbox: list[int] | None = None
        diff_result: dict[str, Any] | None = None

        if same_dimensions:
            changed_pixels = 0
            min_x, min_y = before_width, before_height
            max_x = max_y = -1
            diff_rgb = bytearray(len(before_rgb))
            pixel_count = before_width * before_height
            for pixel_index in range(pixel_count):
                offset = pixel_index * 3
                dr = abs(before_rgb[offset] - after_rgb[offset])
                dg = abs(before_rgb[offset + 1] - after_rgb[offset + 1])
                db = abs(before_rgb[offset + 2] - after_rgb[offset + 2])
                diff_rgb[offset : offset + 3] = bytes((dr, dg, db))
                if dr or dg or db:
                    changed_pixels += 1
                    x = pixel_index % before_width
                    y = pixel_index // before_width
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
            changed_ratio = changed_pixels / float(pixel_count)
            if changed_pixels:
                changed_bbox = [min_x, min_y, max_x + 1, max_y + 1]
                diff_png = encode_rgb(before_width, before_height, bytes(diff_rgb))
                diff_capture = GuiCapture(
                    data=diff_png,
                    mime_type="image/png",
                    width=before_width,
                    height=before_height,
                    capture_method="pixel_difference",
                    occlusion_safe=True,
                )
                diff_result = await self._persist_capture(
                    engineering,
                    run_id,
                    None,
                    diff_capture,
                    operation="pcs.visual_diff",
                    label="visual difference",
                    turn_id=turn_id,
                    action_id=uuid.uuid4().hex,
                    extra_payload={
                        "before_artifact_id": before_artifact_id,
                        "after_artifact_id": after_artifact_id,
                    },
                )

        result = {
            "session_id": session_id,
            "run_id": run_id,
            "before_artifact_id": before_artifact_id,
            "after_artifact_id": after_artifact_id,
            "same_dimensions": same_dimensions,
            "before_dimensions": [before_width, before_height],
            "after_dimensions": [after_width, after_height],
            "changed_pixel_ratio": changed_ratio,
            "changed_bbox": changed_bbox,
            "identical": bool(same_dimensions and changed_ratio == 0.0),
            "diff_artifact": (
                {key: value for key, value in diff_result.items() if key != "image_base64"}
                if diff_result
                else None
            ),
        }
        evidence = await self._evidence.record(
            session_id,
            category="gui",
            operation="pcs.visual_compare",
            status="COMPLETED",
            payload=result,
            turn_id=turn_id,
            action_id=action_id or uuid.uuid4().hex,
        )
        response = {
            **result,
            "evidence_id": evidence.id,
            "evidence_action_id": evidence.action_id,
            "turn_id": turn_id,
        }
        if diff_result:
            response["image_base64"] = diff_result["image_base64"]
        return response