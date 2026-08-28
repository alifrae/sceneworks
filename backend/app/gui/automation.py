"""Controlled accessibility automation for managed PCS windows (WP18).

The provider uses Windows UI Automation (UIA) through the built-in .NET
UIAutomationClient assembly. SceneWorks supplies a fixed PowerShell program and
passes only JSON data through an environment variable; MCP clients never provide
script text, process ids, HWNDs or screen coordinates.

Control ids are ephemeral UIA runtime-id references bound to one managed PCS
window. Every action re-resolves the id under that window before invoking a UIA
pattern, so a forged/stale id cannot escape to another application.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from app.gui.provider import GuiWindow


class GuiAutomationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuiControl:
    control_id: str
    window_id: str
    name: str
    automation_id: str
    control_type: str
    enabled: bool
    offscreen: bool
    left: int
    top: int
    right: int
    bottom: int
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class GuiActionResult:
    action: str
    control: GuiControl
    provider: str


class GuiAutomationProvider(Protocol):
    def list_controls(self, window: GuiWindow, max_controls: int = 500) -> list[GuiControl]: ...

    def invoke(self, window: GuiWindow, control_id: str) -> GuiActionResult: ...

    def set_value(self, window: GuiWindow, control_id: str, value: str) -> GuiActionResult: ...

    def select(self, window: GuiWindow, control_id: str) -> GuiActionResult: ...

    def toggle(self, window: GuiWindow, control_id: str) -> GuiActionResult: ...


def _b64url_encode(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> dict[str, Any]:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode((value + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - normalize opaque id errors
        raise GuiAutomationError("invalid GUI control id") from exc
    if not isinstance(payload, dict):
        raise GuiAutomationError("invalid GUI control id")
    return payload


def encode_control_id(window_id: str, runtime_id: list[int]) -> str:
    if not runtime_id or len(runtime_id) > 64:
        raise GuiAutomationError("UI Automation control has an invalid runtime id")
    return "uia:" + _b64url_encode(
        {"v": 1, "window_id": window_id, "runtime_id": [int(item) for item in runtime_id]}
    )


def decode_control_id(control_id: str) -> tuple[str, list[int]]:
    value = (control_id or "").strip()
    if not value.startswith("uia:") or len(value) > 1600:
        raise GuiAutomationError("invalid GUI control id")
    payload = _b64url_decode(value[4:])
    if payload.get("v") != 1:
        raise GuiAutomationError("unsupported GUI control id version")
    window_id = str(payload.get("window_id") or "")
    runtime_id = payload.get("runtime_id")
    if not window_id.startswith("w:") or not isinstance(runtime_id, list):
        raise GuiAutomationError("invalid GUI control id")
    try:
        values = [int(item) for item in runtime_id]
    except (TypeError, ValueError) as exc:
        raise GuiAutomationError("invalid GUI control id") from exc
    if not values or len(values) > 64:
        raise GuiAutomationError("invalid GUI control id")
    return window_id, values


# Fixed internal adapter. The only dynamic input is parsed JSON from an
# environment variable. No caller-controlled PowerShell expression is evaluated.
_UIA_SCRIPT = r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$payloadBytes = [Convert]::FromBase64String($env:SCENEWORKS_UIA_PAYLOAD)
$payloadText = [Text.Encoding]::UTF8.GetString($payloadBytes)
$p = $payloadText | ConvertFrom-Json
$hwnd = [Int64]$p.hwnd
$root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$hwnd)
if ($null -eq $root) { throw "managed PCS window is unavailable to Windows UI Automation" }

function Runtime-Key($element) {
    return (($element.GetRuntimeId() | ForEach-Object { [string][int]$_ }) -join ",")
}

function Pattern-Names($element) {
    $result = New-Object System.Collections.Generic.List[string]
    $pairs = @(
        @("invoke", [Windows.Automation.InvokePattern]::Pattern),
        @("value", [Windows.Automation.ValuePattern]::Pattern),
        @("selection_item", [Windows.Automation.SelectionItemPattern]::Pattern),
        @("toggle", [Windows.Automation.TogglePattern]::Pattern)
    )
    foreach ($pair in $pairs) {
        try {
            $null = $element.GetCurrentPattern($pair[1])
            $result.Add([string]$pair[0])
        } catch { }
    }
    return @($result)
}

function Control-Object($element) {
    $rect = $element.Current.BoundingRectangle
    return [PSCustomObject]@{
        runtime_id = @($element.GetRuntimeId() | ForEach-Object { [int]$_ })
        name = [string]$element.Current.Name
        automation_id = [string]$element.Current.AutomationId
        control_type = [string]$element.Current.ControlType.ProgrammaticName
        enabled = [bool]$element.Current.IsEnabled
        offscreen = [bool]$element.Current.IsOffscreen
        left = [int][Math]::Round($rect.Left)
        top = [int][Math]::Round($rect.Top)
        right = [int][Math]::Round($rect.Right)
        bottom = [int][Math]::Round($rect.Bottom)
        patterns = @(Pattern-Names $element)
    }
}

$all = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)

if ([string]$p.operation -eq "list") {
    $limit = [Math]::Max(1, [Math]::Min([int]$p.max_controls, 1000))
    $items = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $all.Count -and $items.Count -lt $limit; $i++) {
        $items.Add((Control-Object $all.Item($i)))
    }
    @($items) | ConvertTo-Json -Compress -Depth 8
    exit 0
}

$targetKey = (@($p.runtime_id | ForEach-Object { [string][int]$_ }) -join ",")
$target = $null
for ($i = 0; $i -lt $all.Count; $i++) {
    $candidate = $all.Item($i)
    if ((Runtime-Key $candidate) -eq $targetKey) {
        $target = $candidate
        break
    }
}
if ($null -eq $target) { throw "GUI control id is stale or not inside the managed PCS window" }
if (-not $target.Current.IsEnabled) { throw "target GUI control is disabled" }

switch ([string]$p.operation) {
    "invoke" {
        $pattern = [Windows.Automation.InvokePattern]$target.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
        $pattern.Invoke()
    }
    "set_value" {
        $pattern = [Windows.Automation.ValuePattern]$target.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
        if ($pattern.Current.IsReadOnly) { throw "target GUI control value is read-only" }
        $pattern.SetValue([string]$p.value)
    }
    "select" {
        $pattern = [Windows.Automation.SelectionItemPattern]$target.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern)
        $pattern.Select()
    }
    "toggle" {
        $pattern = [Windows.Automation.TogglePattern]$target.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
        $pattern.Toggle()
    }
    default { throw "unsupported GUI automation operation" }
}

(Control-Object $target) | ConvertTo-Json -Compress -Depth 8
'''


class WindowsUiaAutomationProvider:
    """Windows UI Automation provider scoped by an already-validated PCS window."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))

    @staticmethod
    def _require_windows() -> None:
        if sys.platform != "win32":
            raise GuiAutomationError(
                "GUI automation is currently supported only on the Windows SceneWorks host"
            )

    @staticmethod
    def _powershell() -> str:
        executable = (
            shutil.which("powershell.exe")
            or shutil.which("powershell")
            or shutil.which("pwsh.exe")
            or shutil.which("pwsh")
        )
        if not executable:
            raise GuiAutomationError(
                "Windows PowerShell/PowerShell is required for the UI Automation provider"
            )
        return executable

    @staticmethod
    def _hwnd(window: GuiWindow) -> int:
        value = (window.window_id or "").strip().lower()
        if not value.startswith("w:"):
            raise GuiAutomationError("invalid managed PCS window id")
        try:
            hwnd = int(value[2:], 16)
        except ValueError as exc:
            raise GuiAutomationError("invalid managed PCS window id") from exc
        if hwnd <= 0:
            raise GuiAutomationError("invalid managed PCS window id")
        return hwnd

    def _run(self, window: GuiWindow, payload: dict[str, Any]) -> Any:
        self._require_windows()
        payload = {"hwnd": self._hwnd(window), **payload}
        encoded_payload = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        script = base64.b64encode(_UIA_SCRIPT.encode("utf-16le")).decode("ascii")
        env = {
            "SCENEWORKS_UIA_PAYLOAD": encoded_payload,
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
            "PATH": os.environ.get("PATH", ""),
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
        }
        try:
            completed = subprocess.run(
                [
                    self._powershell(),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    script,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                env=env,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GuiAutomationError("Windows UI Automation operation timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "UI Automation failed").strip()
            if len(detail) > 2000:
                detail = detail[-2000:]
            raise GuiAutomationError(detail)
        text = completed.stdout.strip()
        if not text:
            raise GuiAutomationError("Windows UI Automation returned no result")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GuiAutomationError("Windows UI Automation returned invalid JSON") from exc

    @staticmethod
    def _control(window: GuiWindow, item: dict[str, Any]) -> GuiControl:
        runtime_id = item.get("runtime_id")
        if not isinstance(runtime_id, list):
            raise GuiAutomationError("Windows UI Automation returned an invalid runtime id")
        return GuiControl(
            control_id=encode_control_id(window.window_id, [int(value) for value in runtime_id]),
            window_id=window.window_id,
            name=str(item.get("name") or ""),
            automation_id=str(item.get("automation_id") or ""),
            control_type=str(item.get("control_type") or ""),
            enabled=bool(item.get("enabled", False)),
            offscreen=bool(item.get("offscreen", False)),
            left=int(item.get("left") or 0),
            top=int(item.get("top") or 0),
            right=int(item.get("right") or 0),
            bottom=int(item.get("bottom") or 0),
            patterns=tuple(sorted(str(value) for value in (item.get("patterns") or []))),
        )

    def list_controls(self, window: GuiWindow, max_controls: int = 500) -> list[GuiControl]:
        result = self._run(
            window,
            {"operation": "list", "max_controls": max(1, min(int(max_controls), 1000))},
        )
        if isinstance(result, dict):
            rows = [result]
        elif isinstance(result, list):
            rows = result
        else:
            raise GuiAutomationError("Windows UI Automation returned an invalid control list")
        return [self._control(window, row) for row in rows if isinstance(row, dict)]

    def _action(
        self,
        window: GuiWindow,
        control_id: str,
        operation: str,
        *,
        value: str | None = None,
    ) -> GuiActionResult:
        encoded_window, runtime_id = decode_control_id(control_id)
        if encoded_window != window.window_id:
            raise GuiAutomationError(
                "GUI control id belongs to a different managed PCS window"
            )
        payload: dict[str, Any] = {"operation": operation, "runtime_id": runtime_id}
        if value is not None:
            payload["value"] = value
        result = self._run(window, payload)
        if not isinstance(result, dict):
            raise GuiAutomationError("Windows UI Automation returned an invalid action result")
        return GuiActionResult(
            action=operation,
            control=self._control(window, result),
            provider="windows_uia",
        )

    def invoke(self, window: GuiWindow, control_id: str) -> GuiActionResult:
        return self._action(window, control_id, "invoke")

    def set_value(self, window: GuiWindow, control_id: str, value: str) -> GuiActionResult:
        if len(value) > 16_000:
            raise GuiAutomationError("GUI value exceeds the 16000-character limit")
        return self._action(window, control_id, "set_value", value=value)

    def select(self, window: GuiWindow, control_id: str) -> GuiActionResult:
        return self._action(window, control_id, "select")

    def toggle(self, window: GuiWindow, control_id: str) -> GuiActionResult:
        return self._action(window, control_id, "toggle")
