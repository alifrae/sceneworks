"""WP17 managed PCS GUI-observation and visual-evidence regression tests."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from app.gui import GuiCapture, GuiWindow
from app.gui.png import decode_rgb, encode_rgb


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


async def _project(client, git_repo: Path, name: str = "wp17-gui") -> dict:
    response = await client.post(
        "/api/projects",
        json={"name": name, "repository_path": str(git_repo)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _session(client, project_id: int, permissions: list[str], request_id: int = 20):
    return await _rpc(
        client,
        "sceneworks.engineering_session.create",
        {"project_id": project_id, "permissions": permissions},
        request_id=request_id,
    )


def _profile() -> dict:
    return {
        "command": sys.executable,
        "args": ["-u", "-c", "import time; print('gui-ready', flush=True); time.sleep(60)"],
        "startup_timeout_seconds": 2,
    }


class FakeGuiProvider:
    def __init__(self) -> None:
        self.rgb = bytes(
            [
                10, 20, 30,
                40, 50, 60,
                70, 80, 90,
                100, 110, 120,
            ]
        )
        self.last_pid: int | None = None

    def list_windows(self, pid: int) -> list[GuiWindow]:
        self.last_pid = pid
        return [
            GuiWindow(
                window_id="w:101",
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
            ),
            GuiWindow(
                window_id="w:102",
                pid=pid,
                title="Runtime warning",
                class_name="#32770",
                visible=True,
                enabled=True,
                left=30,
                top=40,
                right=32,
                bottom=42,
                is_dialog=True,
                owner_window_id="w:101",
            ),
        ]

    def capture_window(self, window: GuiWindow) -> GuiCapture:
        assert window.window_id in {"w:101", "w:102"}
        return GuiCapture(
            data=encode_rgb(2, 2, self.rgb),
            mime_type="image/png",
            width=2,
            height=2,
            capture_method="fake-test-provider",
            occlusion_safe=True,
        )


async def _start_gui_session(client, context, git_repo):
    context.settings.mcp_mode = "advanced"
    project = await _project(client, git_repo)
    config = {
        "default_profile": "gui",
        "profiles": {"gui": _profile()},
        "runbooks": {},
        "asset_roots": {},
    }
    put = await client.put(f"/api/projects/{project['id']}/pcs-control", json=config)
    assert put.status_code == 200, put.text
    created = await _session(
        client,
        project["id"],
        ["repository_read", "process_control", "gui_observe"],
    )
    session_id = created["session"]["id"]
    started = await _rpc(
        client,
        "sceneworks.pcs.start",
        {"session_id": session_id, "wait_for_health": False},
        request_id=21,
    )
    assert started["run"]["status"] == "RUNNING"
    return project, session_id, started["run"]


def test_wp17_png_codec_round_trip():
    rgb = bytes([0, 1, 2, 10, 11, 12, 250, 251, 252, 20, 30, 40])
    encoded = encode_rgb(2, 2, rgb)
    width, height, decoded = decode_rgb(encoded)
    assert (width, height) == (2, 2)
    assert decoded == rgb


async def test_wp17_managed_windows_screenshot_visual_compare_and_persistence(
    client, context, git_repo
):
    _project_row, session_id, run = await _start_gui_session(client, context, git_repo)
    provider = FakeGuiProvider()
    context.gui_evidence._provider = provider

    try:
        windows = await _rpc(
            client,
            "sceneworks.pcs.windows",
            {"session_id": session_id},
            request_id=22,
        )
        assert provider.last_pid == run["pid"]
        assert [row["window_id"] for row in windows["windows"]] == ["w:101", "w:102"]
        assert all("pid" not in row for row in windows["windows"])

        dialogs = await _rpc(
            client,
            "sceneworks.pcs.dialogs",
            {"session_id": session_id},
            request_id=23,
        )
        assert len(dialogs["windows"]) == 1
        assert dialogs["windows"][0]["window_id"] == "w:102"

        before, before_images = await _rich_rpc(
            client,
            "sceneworks.pcs.screenshot",
            {"session_id": session_id, "label": "before"},
            request_id=24,
        )
        assert len(before_images) == 1
        assert before["width"] == 2 and before["height"] == 2
        assert before["window"]["window_id"] == "w:101"
        assert before["capture_method"] == "fake-test-provider"
        assert "storage_key" not in before
        assert "image_base64" not in before
        assert base64.b64decode(before_images[0]["data"]) == encode_rgb(2, 2, provider.rgb)

        provider.rgb = bytes(
            [
                10, 20, 30,
                255, 50, 60,
                70, 80, 90,
                100, 110, 120,
            ]
        )
        after, after_images = await _rich_rpc(
            client,
            "sceneworks.pcs.screenshot",
            {"session_id": session_id, "label": "after"},
            request_id=25,
        )
        assert len(after_images) == 1

        # Persisted visual evidence must remain usable after the observed PCS
        # process stops; only fresh observation requires a live managed PID.
        stopped = await _rpc(
            client,
            "sceneworks.pcs.stop",
            {"session_id": session_id},
            request_id=26,
        )
        assert stopped["run"]["status"] == "STOPPED"

        comparison, diff_images = await _rich_rpc(
            client,
            "sceneworks.pcs.visual_compare",
            {
                "session_id": session_id,
                "before_artifact_id": before["artifact_id"],
                "after_artifact_id": after["artifact_id"],
            },
            request_id=27,
        )
        assert comparison["identical"] is False
        assert comparison["changed_pixel_ratio"] == 0.25
        assert comparison["changed_bbox"] == [1, 0, 2, 1]
        assert comparison["diff_artifact"]["artifact_kind"] == "visual_diff"
        assert len(diff_images) == 1

        artifacts = await _rpc(
            client,
            "sceneworks.pcs.gui_artifacts",
            {"session_id": session_id, "limit": 20},
            request_id=28,
        )
        ids = {row["artifact_id"] for row in artifacts["artifacts"]}
        assert before["artifact_id"] in ids
        assert after["artifact_id"] in ids
        assert comparison["diff_artifact"]["artifact_id"] in ids
        assert all("storage_key" not in row for row in artifacts["artifacts"])

        fetched, fetched_images = await _rich_rpc(
            client,
            "sceneworks.pcs.gui_artifact",
            {"session_id": session_id, "artifact_id": before["artifact_id"]},
            request_id=29,
        )
        assert fetched["sha256"] == before["sha256"]
        assert len(fetched_images) == 1

        evidence = await _rpc(
            client,
            "sceneworks.engineering_session.evidence",
            {"session_id": session_id, "category": "gui", "limit": 50},
            request_id=30,
        )
        operations = {row["operation"] for row in evidence["evidence"]}
        assert {
            "pcs.windows",
            "pcs.dialogs",
            "pcs.screenshot",
            "pcs.visual_diff",
            "pcs.visual_compare",
        }.issubset(operations)
    finally:
        status = await _rpc(
            client,
            "sceneworks.pcs.status",
            {"session_id": session_id},
            request_id=90,
        )
        if (status.get("run") or {}).get("status") in {"STARTING", "RUNNING", "STOPPING"}:
            await _rpc(
                client,
                "sceneworks.pcs.stop",
                {"session_id": session_id},
                request_id=91,
            )


async def test_wp17_gui_observation_is_permission_gated_and_not_desktop_generic(
    client, context, git_repo
):
    context.settings.mcp_mode = "advanced"
    project = await _project(client, git_repo, "wp17-permission")
    put = await client.put(
        f"/api/projects/{project['id']}/pcs-control",
        json={
            "default_profile": "gui",
            "profiles": {"gui": _profile()},
            "runbooks": {},
            "asset_roots": {},
        },
    )
    assert put.status_code == 200, put.text
    created = await _session(
        client,
        project["id"],
        ["repository_read", "process_control"],
        request_id=40,
    )
    session_id = created["session"]["id"]
    await _rpc(
        client,
        "sceneworks.pcs.start",
        {"session_id": session_id, "wait_for_health": False},
        request_id=41,
    )
    context.gui_evidence._provider = FakeGuiProvider()
    try:
        error = await _rpc_error(
            client,
            "sceneworks.pcs.windows",
            {"session_id": session_id},
            request_id=42,
        )
        assert "does not grant gui_observe" in error["structuredContent"]["error"]

        listed = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 43, "method": "tools/list", "params": {}},
        )
        tools = {row["name"] for row in listed.json()["result"]["tools"]}
        assert "sceneworks.pcs.screenshot" in tools
        assert "sceneworks.pcs.windows" in tools
        assert "sceneworks.desktop.screenshot" not in tools
        assert "sceneworks.gui.click" not in tools
        assert "sceneworks.gui.keyboard" not in tools
    finally:
        await _rpc(
            client,
            "sceneworks.pcs.stop",
            {"session_id": session_id},
            request_id=44,
        )
