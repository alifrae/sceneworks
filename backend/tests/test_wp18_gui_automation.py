"""WP18 controlled managed-PCS GUI automation regression tests."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from app.gui import GuiActionResult, GuiCapture, GuiControl, GuiWindow, encode_control_id
from app.gui.automation import GuiAutomationError
from app.gui.png import encode_rgb


async def _rpc(client, name: str, arguments: dict, request_id: int = 1):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


async def _rpc_error(client, name: str, arguments: dict, request_id: int = 1):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True, result
    return result


async def _rich_rpc(client, name: str, arguments: dict, request_id: int = 1):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    images = [part for part in result["content"] if part.get("type") == "image"]
    return result["structuredContent"], images


async def _project(client, git_repo: Path, name: str) -> dict:
    response = await client.post(
        "/api/projects",
        json={"name": name, "repository_path": str(git_repo)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _profile() -> dict:
    return {
        "command": sys.executable,
        "args": ["-u", "-c", "import time; print('wp18-ready', flush=True); time.sleep(60)"],
        "startup_timeout_seconds": 2,
    }


class FakeObservationProvider:
    def __init__(self) -> None:
        self.rgb = bytes(
            [
                10, 20, 30,
                40, 50, 60,
                70, 80, 90,
                100, 110, 120,
            ]
        )
        self.capture_count = 0
        self.fail_after_capture = False

    def list_windows(self, pid: int) -> list[GuiWindow]:
        return [
            GuiWindow(
                window_id="w:201",
                pid=pid,
                title="Point Cloud Studio",
                class_name="QtWindow",
                visible=True,
                enabled=True,
                left=10,
                top=20,
                right=12,
                bottom=22,
                is_dialog=False,
            )
        ]

    def capture_window(self, window: GuiWindow) -> GuiCapture:
        self.capture_count += 1
        if self.fail_after_capture and self.capture_count >= 2:
            from app.gui import GuiObservationError

            raise GuiObservationError("simulated after-capture failure")
        return GuiCapture(
            data=encode_rgb(2, 2, self.rgb),
            mime_type="image/png",
            width=2,
            height=2,
            capture_method="fake-wp18-observation",
            occlusion_safe=True,
        )


class FakeAutomationProvider:
    def __init__(self, observation: FakeObservationProvider) -> None:
        self.observation = observation
        self.calls: list[tuple[str, str]] = []
        self.control = GuiControl(
            control_id=encode_control_id("w:201", [42, 7, 9]),
            window_id="w:201",
            name="Play",
            automation_id="pcs.play",
            control_type="ControlType.Button",
            enabled=True,
            offscreen=False,
            left=10,
            top=20,
            right=11,
            bottom=21,
            patterns=("invoke", "selection_item", "toggle", "value"),
        )

    def list_controls(self, window: GuiWindow, max_controls: int = 500) -> list[GuiControl]:
        assert window.window_id == "w:201"
        return [self.control][:max_controls]

    def _mutate(self, action: str, control_id: str) -> GuiActionResult:
        if control_id != self.control.control_id:
            raise GuiAutomationError("stale fake control")
        self.calls.append((action, control_id))
        pixels = bytearray(self.observation.rgb)
        pixels[3] = (pixels[3] + 1) % 256
        self.observation.rgb = bytes(pixels)
        return GuiActionResult(action=action, control=self.control, provider="fake_uia")

    def invoke(self, window: GuiWindow, control_id: str) -> GuiActionResult:
        return self._mutate("invoke", control_id)

    def set_value(self, window: GuiWindow, control_id: str, value: str) -> GuiActionResult:
        assert value
        return self._mutate("set_value", control_id)

    def select(self, window: GuiWindow, control_id: str) -> GuiActionResult:
        return self._mutate("select", control_id)

    def toggle(self, window: GuiWindow, control_id: str) -> GuiActionResult:
        return self._mutate("toggle", control_id)


async def _start(
    client,
    context,
    git_repo,
    *,
    name: str,
    permissions: list[str],
):
    context.settings.mcp_mode = "advanced"
    project = await _project(client, git_repo, name)
    configured = await client.put(
        f"/api/projects/{project['id']}/pcs-control",
        json={
            "default_profile": "gui",
            "profiles": {"gui": _profile()},
            "runbooks": {},
            "asset_roots": {},
        },
    )
    assert configured.status_code == 200, configured.text
    created = await _rpc(
        client,
        "sceneworks.engineering_session.create",
        {"project_id": project["id"], "permissions": permissions},
        request_id=10,
    )
    session_id = created["session"]["id"]
    started = await _rpc(
        client,
        "sceneworks.pcs.start",
        {"session_id": session_id, "wait_for_health": False},
        request_id=11,
    )
    assert started["run"]["status"] == "RUNNING"
    return session_id


async def _stop(client, session_id: int, request_id: int = 90):
    status = await _rpc(
        client,
        "sceneworks.pcs.status",
        {"session_id": session_id},
        request_id=request_id,
    )
    if (status.get("run") or {}).get("status") in {"STARTING", "RUNNING", "STOPPING"}:
        await _rpc(
            client,
            "sceneworks.pcs.stop",
            {"session_id": session_id},
            request_id=request_id + 1,
        )


async def test_wp18_uia_control_discovery_invoke_and_value_evidence(
    client, context, git_repo
):
    session_id = await _start(
        client,
        context,
        git_repo,
        name="wp18-automation",
        permissions=[
            "repository_read",
            "process_control",
            "gui_observe",
            "gui_automate",
        ],
    )
    observation = FakeObservationProvider()
    automation = FakeAutomationProvider(observation)
    context.gui_evidence._provider = observation
    context.gui_automation._provider = automation

    try:
        begun = await _rpc(
            client,
            "sceneworks.engineering_session.begin_turn",
            {"session_id": session_id, "intent": "exercise controlled PCS GUI"},
            request_id=12,
        )
        turn_id = begun["turn"]["id"]

        controls = await _rpc(
            client,
            "sceneworks.pcs.controls",
            {"session_id": session_id, "turn_id": turn_id},
            request_id=13,
        )
        assert len(controls["controls"]) == 1
        target = controls["controls"][0]
        assert target["automation_id"] == "pcs.play"
        assert target["patterns"] == ["invoke", "selection_item", "toggle", "value"]
        assert target["control_id"].startswith("uia:")

        invoked, images = await _rich_rpc(
            client,
            "sceneworks.pcs.gui.invoke",
            {
                "session_id": session_id,
                "control_id": target["control_id"],
                "settle_ms": 0,
                "turn_id": turn_id,
            },
            request_id=14,
        )
        assert invoked["action"] == "invoke"
        assert invoked["provider"] == "fake_uia"
        assert invoked["visual_compare"]["changed_pixel_ratio"] == 0.25
        assert invoked["visual_compare"]["identical"] is False
        assert invoked["before"]["artifact_id"] != invoked["after"]["artifact_id"]
        assert len(images) == 1
        assert base64.b64decode(images[0]["data"]) == encode_rgb(2, 2, observation.rgb)

        secret_value = "C:/private/recording.dat"
        valued, _ = await _rich_rpc(
            client,
            "sceneworks.pcs.gui.set_value",
            {
                "session_id": session_id,
                "control_id": target["control_id"],
                "value": secret_value,
                "settle_ms": 0,
                "turn_id": turn_id,
            },
            request_id=15,
        )
        assert valued["action"] == "set_value"

        evidence = await _rpc(
            client,
            "sceneworks.engineering_session.evidence",
            {"session_id": session_id, "category": "gui", "limit": 100},
            request_id=16,
        )
        rows = evidence["evidence"]
        operations = {row["operation"] for row in rows}
        assert {"pcs.controls", "pcs.gui.invoke", "pcs.gui.set_value", "pcs.visual_compare"}.issubset(operations)
        value_rows = [row for row in rows if row["operation"] == "pcs.gui.set_value"]
        assert value_rows
        payload = value_rows[-1]["payload"]
        assert payload["value_chars"] == len(secret_value)
        assert payload["value_sha256"]
        assert secret_value not in str(payload)
    finally:
        await _stop(client, session_id)


async def test_wp18_gui_automation_requires_explicit_permission_and_has_no_coordinate_tool(
    client, context, git_repo
):
    session_id = await _start(
        client,
        context,
        git_repo,
        name="wp18-permission",
        permissions=["repository_read", "process_control", "gui_observe"],
    )
    observation = FakeObservationProvider()
    context.gui_evidence._provider = observation
    context.gui_automation._provider = FakeAutomationProvider(observation)
    try:
        denied = await _rpc_error(
            client,
            "sceneworks.pcs.controls",
            {"session_id": session_id},
            request_id=30,
        )
        assert "does not grant gui_automate" in denied["structuredContent"]["error"]

        listed = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 31, "method": "tools/list", "params": {}},
        )
        tools = {row["name"] for row in listed.json()["result"]["tools"]}
        assert {
            "sceneworks.pcs.controls",
            "sceneworks.pcs.gui.invoke",
            "sceneworks.pcs.gui.set_value",
            "sceneworks.pcs.gui.select",
            "sceneworks.pcs.gui.toggle",
        }.issubset(tools)
        assert "sceneworks.pcs.gui.click_at" not in tools
        assert "sceneworks.desktop.click" not in tools
        assert "sceneworks.keyboard.type" not in tools
    finally:
        await _stop(client, session_id, request_id=40)


async def test_wp18_action_that_loses_after_evidence_is_reported_unverified(
    client, context, git_repo
):
    session_id = await _start(
        client,
        context,
        git_repo,
        name="wp18-partial",
        permissions=[
            "repository_read",
            "process_control",
            "gui_observe",
            "gui_automate",
        ],
    )
    observation = FakeObservationProvider()
    automation = FakeAutomationProvider(observation)
    observation.fail_after_capture = True
    context.gui_evidence._provider = observation
    context.gui_automation._provider = automation
    try:
        controls = await _rpc(
            client,
            "sceneworks.pcs.controls",
            {"session_id": session_id},
            request_id=50,
        )
        target = controls["controls"][0]["control_id"]
        failed = await _rpc_error(
            client,
            "sceneworks.pcs.gui.invoke",
            {"session_id": session_id, "control_id": target, "settle_ms": 0},
            request_id=51,
        )
        message = failed["structuredContent"]["error"]
        assert "executed but after-action evidence capture failed" in message
        assert automation.calls and automation.calls[0][0] == "invoke"

        evidence = await _rpc(
            client,
            "sceneworks.engineering_session.evidence",
            {"session_id": session_id, "category": "gui", "limit": 100},
            request_id=52,
        )
        action_rows = [
            row for row in evidence["evidence"] if row["operation"] == "pcs.gui.invoke"
        ]
        assert action_rows
        assert action_rows[-1]["status"] == "PARTIAL"
        assert action_rows[-1]["payload"]["action_execution_state"] == "executed_but_unverified"
    finally:
        # Disable the simulated screenshot fault so cleanup/status tooling can
        # complete without unrelated GUI capture failures.
        observation.fail_after_capture = False
        await _stop(client, session_id, request_id=60)
