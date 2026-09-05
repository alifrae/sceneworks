from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.supervisor import SupervisorClient, SupervisorUnavailable


def test_status_decodes_bounded_supervisor_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/status"
        return httpx.Response(200, json={"aggregate_state": "HEALTHY", "components": {}})

    client = SupervisorClient(transport=httpx.MockTransport(handler), token="secret")
    result = asyncio.run(client.status())
    assert result["aggregate_state"] == "HEALTHY"


def test_restart_uses_bearer_token_and_semantic_component_only() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization", "")
        seen["actor"] = request.headers.get("X-SceneWorks-Actor", "")
        seen["path"] = request.url.path
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(202, json={"operation_id": "op-1"})

    client = SupervisorClient(transport=httpx.MockTransport(handler), token="unit-token")
    result = asyncio.run(client.restart("api"))
    assert result == {"operation_id": "op-1"}
    assert seen["authorization"] == "Bearer unit-token"
    assert seen["actor"] == "mcp"
    assert seen["path"] == "/v1/actions/restart"
    assert '"component":"api"' in seen["body"]


def test_restart_all_uses_restart_all_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/actions/restart-all"
        return httpx.Response(202, json={"operation_id": "op-all"})

    client = SupervisorClient(transport=httpx.MockTransport(handler), token="unit-token")
    assert asyncio.run(client.restart("all"))["operation_id"] == "op-all"


def test_invalid_component_is_rejected_before_transport() -> None:
    client = SupervisorClient(token="unit-token")
    with pytest.raises(ValueError):
        asyncio.run(client.restart("database"))  # type: ignore[arg-type]


def test_transport_failure_is_bounded_unavailable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = SupervisorClient(transport=httpx.MockTransport(handler), token="unit-token")
    with pytest.raises(SupervisorUnavailable, match="supervisor is unavailable"):
        asyncio.run(client.status())
