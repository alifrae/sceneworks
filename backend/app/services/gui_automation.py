"""Evidence-first controlled GUI automation for managed PCS (WP18).

Mutation is intentionally narrower than generic desktop automation:

- an active EngineeringSession must grant both ``gui_observe`` and
  ``gui_automate``;
- the target must be a visible window owned by the live SceneWorks-managed PCS
  process;
- callers act on an opaque accessibility control id discovered under that
  window, never on coordinates, arbitrary HWNDs or PIDs;
- a before screenshot is mandatory before mutation;
- an after screenshot plus deterministic WP17 visual comparison are attempted
  after every action and correlated into the WP15 evidence ledger.

If an action executes but after-action evidence cannot be captured, the tool
fails closed as an unverified partial action instead of claiming success.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any, Callable

from app.engineering_models import EngineeringSession
from app.gui import (
    GuiActionResult,
    GuiAutomationError,
    GuiAutomationProvider,
    GuiControl,
    GuiWindow,
    WindowsUiaAutomationProvider,
    decode_control_id,
)
from app.services.engineering_evidence import EngineeringEvidenceError, EngineeringEvidenceService
from app.services.engineering_sessions import EngineeringSessionService
from app.services.gui_evidence import GuiEvidenceError, GuiEvidenceService
from app.services.pcs_control import PcsControlService


class GuiAutomationServiceError(RuntimeError):
    pass


class GuiAutomationService:
    def __init__(
        self,
        engineering_sessions: EngineeringSessionService,
        engineering_evidence: EngineeringEvidenceService,
        pcs_control: PcsControlService,
        gui_evidence: GuiEvidenceService,
        provider: GuiAutomationProvider | None = None,
    ) -> None:
        self._engineering_sessions = engineering_sessions
        self._evidence = engineering_evidence
        self._pcs = pcs_control
        self._gui = gui_evidence
        self._provider: GuiAutomationProvider = provider or WindowsUiaAutomationProvider()

    async def _session(self, session_id: int) -> EngineeringSession:
        row = await self._engineering_sessions.get(session_id)
        if row.status != "ACTIVE":
            raise GuiAutomationServiceError(
                f"engineering session {session_id} is {row.status.lower()}, not active"
            )
        permissions = set(row.permissions or [])
        if "gui_observe" not in permissions:
            raise GuiAutomationServiceError(
                f"engineering session {session_id} does not grant gui_observe; WP18 requires observation so every mutation can produce before/after evidence"
            )
        if "gui_automate" not in permissions:
            raise GuiAutomationServiceError(
                f"engineering session {session_id} does not grant gui_automate"
            )
        return row

    async def _live_window(
        self,
        session_id: int,
        *,
        window_id: str | None,
        turn_id: str | None,
    ) -> tuple[EngineeringSession, int, int, GuiWindow]:
        engineering = await self._session(session_id)
        try:
            await self._evidence.validate_turn(session_id, turn_id)
        except EngineeringEvidenceError as exc:
            raise GuiAutomationServiceError(str(exc)) from exc
        status = await self._pcs.status(session_id)
        run = status.get("run") or {}
        pid = int(run.get("pid") or 0)
        run_id = int(run.get("id") or 0)
        run_status = str(run.get("status") or "")
        if not pid or not run_id or run_status not in {"STARTING", "RUNNING", "STOPPING"}:
            raise GuiAutomationServiceError(
                "GUI automation requires a live SceneWorks-managed PCS run in this EngineeringSession"
            )

        try:
            observed = await self._gui.windows(
                session_id,
                dialogs_only=False,
                visible_only=True,
                turn_id=turn_id,
                action_id=uuid.uuid4().hex,
            )
        except GuiEvidenceError as exc:
            raise GuiAutomationServiceError(str(exc)) from exc
        rows = list(observed.get("windows") or [])
        if window_id:
            selected = next((row for row in rows if row.get("window_id") == window_id), None)
            if selected is None:
                raise GuiAutomationServiceError(
                    "window_id is not a currently visible window owned by the managed PCS process"
                )
        else:
            candidates = [row for row in rows if not row.get("is_dialog")] or rows
            if not candidates:
                raise GuiAutomationServiceError(
                    "managed PCS process has no visible window available for GUI automation"
                )
            selected = max(
                candidates,
                key=lambda row: int((row.get("rect") or {}).get("width") or 0)
                * int((row.get("rect") or {}).get("height") or 0),
            )

        rect = selected.get("rect") or {}
        window = GuiWindow(
            window_id=str(selected.get("window_id") or ""),
            pid=pid,
            title=str(selected.get("title") or ""),
            class_name=str(selected.get("class_name") or ""),
            visible=bool(selected.get("visible", False)),
            enabled=bool(selected.get("enabled", False)),
            left=int(rect.get("left") or 0),
            top=int(rect.get("top") or 0),
            right=int(rect.get("right") or 0),
            bottom=int(rect.get("bottom") or 0),
            is_dialog=bool(selected.get("is_dialog", False)),
            owner_window_id=(
                str(selected.get("owner_window_id"))
                if selected.get("owner_window_id")
                else None
            ),
        )
        if not window.enabled:
            raise GuiAutomationServiceError("selected managed PCS window is disabled")
        return engineering, pid, run_id, window

    @staticmethod
    def _control_dict(control: GuiControl) -> dict[str, Any]:
        return {
            "control_id": control.control_id,
            "window_id": control.window_id,
            "name": control.name,
            "automation_id": control.automation_id,
            "control_type": control.control_type,
            "enabled": control.enabled,
            "offscreen": control.offscreen,
            "rect": {
                "left": control.left,
                "top": control.top,
                "right": control.right,
                "bottom": control.bottom,
                "width": max(0, control.right - control.left),
                "height": max(0, control.bottom - control.top),
            },
            "patterns": list(control.patterns),
        }

    @staticmethod
    def _capture_metadata(capture: dict[str, Any] | None) -> dict[str, Any] | None:
        if capture is None:
            return None
        return {key: value for key, value in capture.items() if key != "image_base64"}

    async def controls(
        self,
        session_id: int,
        *,
        window_id: str | None = None,
        max_controls: int = 500,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        _engineering, _pid, run_id, window = await self._live_window(
            session_id, window_id=window_id, turn_id=turn_id
        )
        try:
            controls = await asyncio.to_thread(
                self._provider.list_controls,
                window,
                max(1, min(int(max_controls), 1000)),
            )
        except GuiAutomationError as exc:
            raise GuiAutomationServiceError(str(exc)) from exc
        public = [self._control_dict(control) for control in controls]
        evidence = await self._evidence.record(
            session_id,
            category="gui",
            operation="pcs.controls",
            status="COMPLETED",
            payload={
                "run_id": run_id,
                "window_id": window.window_id,
                "control_count": len(public),
                "provider": "windows_uia" if isinstance(self._provider, WindowsUiaAutomationProvider) else "injected",
                "controls": public,
            },
            turn_id=turn_id,
            action_id=action_id or uuid.uuid4().hex,
        )
        return {
            "session_id": session_id,
            "run_id": run_id,
            "window_id": window.window_id,
            "controls": public,
            "evidence_id": evidence.id,
            "evidence_action_id": evidence.action_id,
            "turn_id": turn_id,
        }

    async def invoke(
        self,
        session_id: int,
        control_id: str,
        *,
        settle_ms: int = 150,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._perform(
            session_id,
            control_id,
            "invoke",
            self._provider.invoke,
            settle_ms=settle_ms,
            turn_id=turn_id,
            action_id=action_id,
        )

    async def set_value(
        self,
        session_id: int,
        control_id: str,
        value: str,
        *,
        settle_ms: int = 150,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        if len(value) > 16_000:
            raise GuiAutomationServiceError("GUI value exceeds the 16000-character limit")
        return await self._perform(
            session_id,
            control_id,
            "set_value",
            lambda window, target: self._provider.set_value(window, target, value),
            settle_ms=settle_ms,
            turn_id=turn_id,
            action_id=action_id,
            sensitive_value=value,
        )

    async def select(
        self,
        session_id: int,
        control_id: str,
        *,
        settle_ms: int = 150,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._perform(
            session_id,
            control_id,
            "select",
            self._provider.select,
            settle_ms=settle_ms,
            turn_id=turn_id,
            action_id=action_id,
        )

    async def toggle(
        self,
        session_id: int,
        control_id: str,
        *,
        settle_ms: int = 150,
        turn_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._perform(
            session_id,
            control_id,
            "toggle",
            self._provider.toggle,
            settle_ms=settle_ms,
            turn_id=turn_id,
            action_id=action_id,
        )

    async def _capture_after(
        self,
        session_id: int,
        preferred_window_id: str,
        *,
        turn_id: str | None,
        label: str,
    ) -> tuple[dict[str, Any], bool]:
        try:
            capture = await self._gui.screenshot(
                session_id,
                window_id=preferred_window_id,
                label=label,
                turn_id=turn_id,
                action_id=uuid.uuid4().hex,
            )
            return capture, False
        except GuiEvidenceError:
            capture = await self._gui.screenshot(
                session_id,
                window_id=None,
                label=label + " (fallback managed PCS window)",
                turn_id=turn_id,
                action_id=uuid.uuid4().hex,
            )
            return capture, True

    async def _perform(
        self,
        session_id: int,
        control_id: str,
        operation: str,
        action: Callable[[GuiWindow, str], GuiActionResult],
        *,
        settle_ms: int,
        turn_id: str | None,
        action_id: str | None,
        sensitive_value: str | None = None,
    ) -> dict[str, Any]:
        requested_action_id = action_id or uuid.uuid4().hex
        try:
            encoded_window_id, _runtime_id = decode_control_id(control_id)
        except GuiAutomationError as exc:
            raise GuiAutomationServiceError(str(exc)) from exc
        _engineering, _pid, run_id, window = await self._live_window(
            session_id, window_id=encoded_window_id, turn_id=turn_id
        )
        settle_ms = max(0, min(int(settle_ms), 2000))

        # Fail before mutation if objective pre-action evidence cannot be captured.
        try:
            before = await self._gui.screenshot(
                session_id,
                window_id=window.window_id,
                label=f"WP18 before {operation}",
                turn_id=turn_id,
                action_id=uuid.uuid4().hex,
            )
        except GuiEvidenceError as exc:
            raise GuiAutomationServiceError(
                f"GUI action was not executed because pre-action screenshot evidence failed: {exc}"
            ) from exc

        value_evidence: dict[str, Any] = {}
        if sensitive_value is not None:
            value_evidence = {
                "value_chars": len(sensitive_value),
                "value_sha256": hashlib.sha256(sensitive_value.encode("utf-8")).hexdigest(),
            }

        try:
            provider_result = await asyncio.to_thread(action, window, control_id)
        except GuiAutomationError as exc:
            after: dict[str, Any] | None = None
            fallback = False
            try:
                after, fallback = await self._capture_after(
                    session_id,
                    window.window_id,
                    turn_id=turn_id,
                    label=f"WP18 after failed {operation}",
                )
            except (GuiEvidenceError, GuiAutomationServiceError):
                pass
            evidence = await self._evidence.record(
                session_id,
                category="gui",
                operation=f"pcs.gui.{operation}",
                status="FAILED",
                payload={
                    "run_id": run_id,
                    "control_id": control_id,
                    "provider_error": str(exc)[:2000],
                    "before_artifact_id": before.get("artifact_id"),
                    "after_artifact_id": after.get("artifact_id") if after else None,
                    "after_window_fallback": fallback,
                    "action_execution_state": "unknown_after_provider_error",
                    **value_evidence,
                },
                turn_id=turn_id,
                action_id=requested_action_id,
            )
            raise GuiAutomationServiceError(
                f"GUI {operation} failed; evidence_id={evidence.id}: {exc}"
            ) from exc

        if settle_ms:
            await asyncio.sleep(settle_ms / 1000.0)

        try:
            after, fallback = await self._capture_after(
                session_id,
                window.window_id,
                turn_id=turn_id,
                label=f"WP18 after {operation}",
            )
        except (GuiEvidenceError, GuiAutomationServiceError) as exc:
            evidence = await self._evidence.record(
                session_id,
                category="gui",
                operation=f"pcs.gui.{operation}",
                status="PARTIAL",
                payload={
                    "run_id": run_id,
                    "control": self._control_dict(provider_result.control),
                    "provider": provider_result.provider,
                    "before_artifact_id": before.get("artifact_id"),
                    "after_capture_error": str(exc)[:2000],
                    "action_execution_state": "executed_but_unverified",
                    **value_evidence,
                },
                turn_id=turn_id,
                action_id=requested_action_id,
            )
            raise GuiAutomationServiceError(
                f"GUI {operation} executed but after-action evidence capture failed; evidence_id={evidence.id}. Treat the action as unverified."
            ) from exc

        try:
            comparison = await self._gui.compare(
                session_id,
                int(before["artifact_id"]),
                int(after["artifact_id"]),
                turn_id=turn_id,
                action_id=uuid.uuid4().hex,
            )
        except GuiEvidenceError as exc:
            evidence = await self._evidence.record(
                session_id,
                category="gui",
                operation=f"pcs.gui.{operation}",
                status="PARTIAL",
                payload={
                    "run_id": run_id,
                    "control": self._control_dict(provider_result.control),
                    "provider": provider_result.provider,
                    "before_artifact_id": before.get("artifact_id"),
                    "after_artifact_id": after.get("artifact_id"),
                    "visual_compare_error": str(exc)[:2000],
                    "action_execution_state": "executed_with_images_but_uncompared",
                    **value_evidence,
                },
                turn_id=turn_id,
                action_id=requested_action_id,
            )
            raise GuiAutomationServiceError(
                f"GUI {operation} executed and screenshots were captured, but deterministic visual comparison failed; evidence_id={evidence.id}."
            ) from exc

        comparison_public = {
            key: value for key, value in comparison.items() if key != "image_base64"
        }
        payload = {
            "run_id": run_id,
            "control": self._control_dict(provider_result.control),
            "provider": provider_result.provider,
            "before_artifact_id": before.get("artifact_id"),
            "after_artifact_id": after.get("artifact_id"),
            "after_window_fallback": fallback,
            "visual_compare_evidence_id": comparison.get("evidence_id"),
            "changed_pixel_ratio": comparison.get("changed_pixel_ratio"),
            "changed_bbox": comparison.get("changed_bbox"),
            "identical": comparison.get("identical"),
            "action_execution_state": "executed_and_verified",
            **value_evidence,
        }
        evidence = await self._evidence.record(
            session_id,
            category="gui",
            operation=f"pcs.gui.{operation}",
            status="COMPLETED",
            payload=payload,
            turn_id=turn_id,
            action_id=requested_action_id,
        )
        return {
            "session_id": session_id,
            "run_id": run_id,
            "action": operation,
            "control": self._control_dict(provider_result.control),
            "provider": provider_result.provider,
            "before": self._capture_metadata(before),
            "after": self._capture_metadata(after),
            "after_window_fallback": fallback,
            "visual_compare": comparison_public,
            "evidence_id": evidence.id,
            "evidence_action_id": evidence.action_id,
            "turn_id": turn_id,
            # Rich MCP response shows the resulting PCS state, while the before
            # image and pixel diff remain durable retrievable artifacts.
            "image_base64": after.get("image_base64"),
        }
