"""WP16 semantic PCS runtime-control regression tests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


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


async def _project(client, git_repo: Path, name: str = "pcs") -> dict:
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


async def _wait_for_log(client, session_id: int, needle: str, request_id: int = 70):
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        result = await _rpc(
            client,
            "sceneworks.pcs.logs",
            {"session_id": session_id, "contains": needle, "limit": 100},
            request_id=request_id,
        )
        if result["events"]:
            return result
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"PCS log {needle!r} was not captured")
        await asyncio.sleep(0.05)


async def _wait_for_state(client, session_id: int, state: str, request_id: int = 80):
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        result = await _rpc(
            client,
            "sceneworks.pcs.status",
            {"session_id": session_id},
            request_id=request_id,
        )
        run = result.get("run") or {}
        if run.get("status") == state:
            return result
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"PCS did not reach {state}: {run}")
        await asyncio.sleep(0.05)


def _profile(script: str, **extra) -> dict:
    return {
        "command": sys.executable,
        "args": ["-u", "-c", script],
        "startup_timeout_seconds": 2,
        **extra,
    }


async def test_wp16_managed_pcs_lifecycle_logs_health_runtime_state_and_close_guard(
    client, context, git_repo
):
    context.settings.mcp_mode = "advanced"
    project = await _project(client, git_repo, "pcs-lifecycle")
    script = (
        "import pathlib,sys,time; "
        "print('INFO pcs-ready', flush=True); "
        "print('ERROR simulated-runtime-error', file=sys.stderr, flush=True); "
        "pathlib.Path('pcs.log').write_text('captured-file-log\\n'); "
        "time.sleep(60)"
    )
    config = {
        "default_profile": "debug",
        "profiles": {
            "debug": _profile(script, log_paths=["pcs.log"]),
        },
        "runbooks": {},
        "asset_roots": {},
    }
    put = await client.put(f"/api/projects/{project['id']}/pcs-control", json=config)
    assert put.status_code == 200, put.text

    created = await _session(
        client,
        project["id"],
        ["repository_read", "process_control"],
    )
    session_id = created["session"]["id"]
    turn = await _rpc(
        client,
        "sceneworks.engineering_session.begin_turn",
        {"session_id": session_id, "intent": "launch and observe PCS"},
        request_id=21,
    )
    turn_id = turn["turn"]["id"]

    started = await _rpc(
        client,
        "sceneworks.pcs.start",
        {"session_id": session_id, "turn_id": turn_id, "wait_for_health": False},
        request_id=22,
    )
    assert started["run"]["status"] == "RUNNING"
    assert started["run"]["pid"]
    assert started["run"]["profile"] == "debug"

    info_log = await _wait_for_log(client, session_id, "pcs-ready")
    assert info_log["events"][0]["severity"] == "info"
    assert info_log["events"][0]["timestamp"]
    errors = await _rpc(
        client,
        "sceneworks.pcs.errors",
        {"session_id": session_id, "contains": "simulated-runtime-error"},
        request_id=23,
    )
    assert errors["events"]
    assert errors["events"][0]["source"] == "stderr"
    assert errors["events"][0]["severity"] == "error"

    health = await _rpc(
        client,
        "sceneworks.pcs.health",
        {"session_id": session_id, "turn_id": turn_id},
        request_id=24,
    )
    assert health["ready"] is True
    assert health["process"]["status"] == "RUNNING"

    runtime_state = await _rpc(
        client,
        "sceneworks.pcs.runtime_state",
        {"session_id": session_id, "turn_id": turn_id},
        request_id=25,
    )
    assert runtime_state["source"] == "sceneworks_process"
    assert runtime_state["pcs_api_available"] is False
    assert runtime_state["state"]["active_recording"] is None
    assert "requires a configured PCS API" in runtime_state["limitations"]

    close_error = await _rpc_error(
        client,
        "sceneworks.engineering_session.close",
        {"session_id": session_id},
        request_id=26,
    )
    assert "pcs.stop" in json.dumps(close_error).lower()

    stopped = await _rpc(
        client,
        "sceneworks.pcs.stop",
        {"session_id": session_id, "turn_id": turn_id},
        request_id=27,
    )
    assert stopped["run"]["status"] == "STOPPED"
    assert stopped["run"]["finished_at"]

    evidence = await _rpc(
        client,
        "sceneworks.engineering_session.evidence",
        {"session_id": session_id, "turn_id": turn_id, "limit": 200},
        request_id=28,
    )
    rows = evidence["evidence"]
    assert any(row["category"] == "pcs_log" for row in rows)
    stopped_row = next(row for row in rows if row["operation"] == "pcs.stopped")
    log_artifact = stopped_row["payload"]["artifacts"]["logs"][0]
    assert log_artifact["path"] == "pcs.log"
    assert log_artifact["exists"] is True
    assert log_artifact["sha256"]


async def test_wp16_nonzero_exit_is_crash_evidence(client, context, git_repo):
    context.settings.mcp_mode = "advanced"
    project = await _project(client, git_repo, "pcs-crash")
    config = {
        "default_profile": "crash",
        "profiles": {
            "crash": _profile(
                "import sys; print('FATAL deliberate-crash', file=sys.stderr, flush=True); sys.exit(7)"
            )
        },
    }
    response = await client.put(
        f"/api/projects/{project['id']}/pcs-control", json=config
    )
    assert response.status_code == 200, response.text
    created = await _session(client, project["id"], ["process_control"], 30)
    session_id = created["session"]["id"]

    await _rpc(
        client,
        "sceneworks.pcs.start",
        {"session_id": session_id, "wait_for_health": False},
        request_id=31,
    )
    status = await _wait_for_state(client, session_id, "CRASHED", request_id=32)
    assert status["run"]["exit_code"] == 7

    logs = await _wait_for_log(client, session_id, "deliberate-crash", 33)
    assert logs["events"][0]["severity"] == "critical"
    evidence = await _rpc(
        client,
        "sceneworks.engineering_session.evidence",
        {"session_id": session_id, "category": "pcs", "limit": 100},
        request_id=34,
    )
    exit_row = next(row for row in evidence["evidence"] if row["operation"] == "pcs.exit")
    assert exit_row["status"] == "FAILED"
    assert exit_row["payload"]["exit_code"] == 7
    assert exit_row["payload"]["state"] == "CRASHED"


async def test_wp16_assets_are_alias_scoped_and_runbooks_are_deterministic(
    client, context, git_repo, tmp_path
):
    context.settings.mcp_mode = "advanced"
    project = await _project(client, git_repo, "pcs-assets")
    assets = tmp_path / "recordings"
    assets.mkdir()
    recording = assets / "sample.dat"
    recording.write_text("known-recording", encoding="utf-8")
    config = {
        "profiles": {},
        "asset_roots": {"recordings": {"path": str(assets), "read_only": True}},
        "runbooks": {
            "asset-smoke": {
                "description": "read one governed recording",
                "steps": [
                    {
                        "action": "command",
                        "command": sys.executable,
                        "args": [
                            "-c",
                            "import sys; print(open(sys.argv[1], encoding='utf-8').read())",
                            "{{asset:recordings:sample.dat}}",
                        ],
                        "expect_exit_code": 0,
                    }
                ],
            }
        },
    }
    configured = await _rpc(
        client,
        "sceneworks.pcs.configure",
        {"project_id": project["id"], "config": config},
        request_id=40,
    )
    serialized = json.dumps(configured)
    assert str(assets) not in serialized
    assert configured["config"]["asset_roots"]["recordings"]["configured"] is True

    created = await _session(
        client,
        project["id"],
        ["repository_read", "shell_execute", "external_asset_read"],
        41,
    )
    session_id = created["session"]["id"]
    listing = await _rpc(
        client,
        "sceneworks.pcs.assets",
        {"session_id": session_id, "asset_root": "recordings"},
        request_id=42,
    )
    assert listing["entries"][0]["path"] == "sample.dat"
    assert str(assets) not in json.dumps(listing)

    info = await _rpc(
        client,
        "sceneworks.pcs.asset_info",
        {
            "session_id": session_id,
            "asset_root": "recordings",
            "path": "sample.dat",
            "include_sha256": True,
        },
        request_id=43,
    )
    assert info["sha256"]
    traversal = await _rpc_error(
        client,
        "sceneworks.pcs.asset_info",
        {
            "session_id": session_id,
            "asset_root": "recordings",
            "path": "../outside.dat",
        },
        request_id=44,
    )
    assert "escapes" in json.dumps(traversal).lower()

    verified = await _rpc(
        client,
        "sceneworks.pcs.run_verification",
        {"session_id": session_id, "runbook": "asset-smoke"},
        request_id=45,
    )
    assert verified["passed"] is True
    assert "known-recording" in verified["steps"][0]["stdout"]
    assert str(assets) not in json.dumps(verified)

    evidence = await _rpc(
        client,
        "sceneworks.engineering_session.evidence",
        {"session_id": session_id, "category": "verification", "limit": 100},
        request_id=46,
    )
    assert evidence["evidence"]
    assert str(assets) not in json.dumps(evidence)
    command = next(
        row for row in evidence["evidence"] if row["operation"] == "pcs.runbook.command"
    )
    assert command["payload"]["asset_refs"][0]["asset_root"] == "recordings"
    assert command["payload"]["asset_refs"][0]["path"] == "sample.dat"


async def test_wp16_config_security_catalog_and_project_purge(client, context, git_repo, tmp_path):
    context.settings.mcp_mode = "advanced"
    project = await _project(client, git_repo, "pcs-config")
    assets = tmp_path / "assets"
    assets.mkdir()

    invalid = await client.put(
        f"/api/projects/{project['id']}/pcs-control",
        json={
            "profiles": {
                "bad": {
                    "command": sys.executable,
                    "expected_ports": [{"host": "example.com", "port": 443}],
                }
            }
        },
    )
    assert invalid.status_code == 422

    secret = await client.put(
        f"/api/projects/{project['id']}/pcs-control",
        json={
            "profiles": {
                "bad": {
                    "command": sys.executable,
                    "environment": {"API_TOKEN": "must-not-persist"},
                }
            }
        },
    )
    assert secret.status_code == 422

    valid = await client.put(
        f"/api/projects/{project['id']}/pcs-control",
        json={
            "profiles": {
                "safe": {
                    "command": sys.executable,
                    "api_base_url": "http://127.0.0.1:65530",
                    "health_path": "/health",
                }
            },
            "asset_roots": {"fixtures": {"path": str(assets)}},
        },
    )
    assert valid.status_code == 200, valid.text

    observe = await _rpc(
        client,
        "sceneworks.pcs.get_config",
        {"project_id": project["id"]},
        request_id=50,
    )
    assert str(assets) not in json.dumps(observe)

    catalog_response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 51, "method": "tools/list", "params": {}},
    )
    assert catalog_response.status_code == 200
    tools = {
        item["name"]: item
        for item in catalog_response.json()["result"]["tools"]
    }
    for name in (
        "sceneworks.pcs.start",
        "sceneworks.pcs.stop",
        "sceneworks.pcs.restart",
        "sceneworks.pcs.status",
        "sceneworks.pcs.logs",
        "sceneworks.pcs.errors",
        "sceneworks.pcs.health",
        "sceneworks.pcs.runtime_state",
        "sceneworks.pcs.assets",
        "sceneworks.pcs.run_verification",
    ):
        assert name in tools
    create_schema = tools["sceneworks.engineering_session.create"]["inputSchema"]
    assert "external_asset_read" in create_schema["properties"]["permissions"]["items"]["enum"]

    blocked = await client.delete(f"/api/projects/{project['id']}")
    assert blocked.status_code == 409
    purged = await client.delete(
        f"/api/projects/{project['id']}?purge_history=true"
    )
    assert purged.status_code == 204, purged.text
