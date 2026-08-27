"""HTTP transport for the SceneWorks MCP reasoning interface (WP11)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app import __version__
from app.mcp import SceneWorksMCPServer

router = APIRouter(tags=["mcp"])


@router.get("/mcp")
async def mcp_info(request: Request) -> dict[str, Any]:
    """Human-readable discovery for setup/troubleshooting.

    MCP clients use POST /mcp. This GET intentionally contains no project data.
    """
    ctx = request.app.state.context
    if not ctx.settings.mcp_enabled:
        raise HTTPException(status_code=404, detail="MCP interface is disabled")
    mode = ctx.settings.effective_mcp_mode
    return {
        "name": "SceneWorks",
        "version": __version__,
        "endpoint": "/mcp",
        "transport": "streamable HTTP / JSON-RPC",
        "mode": mode,
        "action_tools_enabled": mode in {"standard", "advanced"},
        "advanced_agent_sessions_enabled": mode == "advanced",
        "security": (
            "SceneWorks does not add OAuth at this endpoint. Keep it local/use a "
            "trusted tunnel, or put authenticated TLS infrastructure in front of it. "
            "Advanced shell execution is worktree-cwd gated but is not an OS sandbox."
        ),
    }


@router.post("/mcp")
async def mcp_rpc(request: Request) -> Response:
    ctx = request.app.state.context
    if not ctx.settings.mcp_enabled:
        raise HTTPException(status_code=404, detail="MCP interface is disabled")

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - transport validation boundary
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"invalid JSON: {exc}"},
            },
        )

    method_header = request.headers.get("Mcp-Method")
    name_header = request.headers.get("Mcp-Name")
    protocol_header = request.headers.get("MCP-Protocol-Version")

    # Current MCP clients send routing headers for stateless HTTP. Validate them
    # when present while remaining compatible with legacy clients that omit them.
    if isinstance(payload, dict):
        method = payload.get("method")
        if method_header and method and method_header != method:
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {
                        "code": -32600,
                        "message": "Mcp-Method header does not match JSON-RPC method",
                    },
                },
            )
        if name_header and method == "tools/call":
            call_name = (payload.get("params") or {}).get("name")
            if call_name and name_header != call_name:
                return JSONResponse(
                    status_code=400,
                    content={
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "error": {
                            "code": -32600,
                            "message": "Mcp-Name header does not match tools/call name",
                        },
                    },
                )

    # Unknown protocol versions fail explicitly instead of being silently
    # interpreted under a different contract. Legacy initialize requests often
    # omit the header and negotiate in the request body, which remains supported.
    if protocol_header and protocol_header not in SceneWorksMCPServer_protocols():
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "error": {
                    "code": -32600,
                    "message": f"unsupported MCP protocol version: {protocol_header}",
                },
            },
        )

    body, status = await SceneWorksMCPServer(ctx).handle(payload)
    if body is None:
        return Response(status_code=status)
    return JSONResponse(status_code=status, content=body)


def SceneWorksMCPServer_protocols() -> tuple[str, ...]:
    """Small indirection keeps transport independent of private server fields."""
    from app.mcp.server import SUPPORTED_PROTOCOLS

    return SUPPORTED_PROTOCOLS
