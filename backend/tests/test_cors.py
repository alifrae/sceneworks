"""CORS regression tests: the web client must work from any local origin.

The frontend fetches the API directly from the browser. If the dev server
serves from any origin other than the configured allow-list (a busy port
bumping `next dev` to 3001, opening http://127.0.0.1:3000, ...), every
response is CORS-blocked and the UI reports "TypeError: Failed to fetch".
These tests pin the localhost/127.0.0.1 any-port allowance.
"""

from __future__ import annotations


async def test_local_origins_any_port_are_allowed(client):
    for origin in (
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://localhost:8123",
    ):
        resp = await client.get("/api/roles", headers={"Origin": origin})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == origin


async def test_local_preflight_is_allowed(client):
    resp = await client.options(
        "/api/tasks",
        headers={
            "Origin": "http://127.0.0.1:3001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:3001"
    assert "POST" in resp.headers.get("access-control-allow-methods", "")


async def test_foreign_origins_stay_blocked(client):
    resp = await client.get("/api/roles", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None


async def test_configured_explicit_origin_still_allowed(client):
    # conftest sets cors_origins=["http://test"]; the regex must not break it.
    resp = await client.get("/api/roles", headers={"Origin": "http://test"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://test"
