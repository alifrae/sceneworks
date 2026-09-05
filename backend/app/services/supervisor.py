"""Narrow client for the loopback-only SceneWorks lifecycle supervisor."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import httpx

SupervisorComponent = Literal["api", "web", "mcp_tunnel", "all"]


class SupervisorUnavailable(RuntimeError):
    """Raised when the local lifecycle supervisor cannot satisfy a request."""


def _default_token_path() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "SceneWorks" / "supervisor" / "token"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "sceneworks" / "supervisor" / "token"
    return Path.home() / ".local" / "share" / "sceneworks" / "supervisor" / "token"


class SupervisorClient:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8020",
        token: str | None = None,
        token_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._token_path = token_path or _default_token_path()
        self._transport = transport
        self._timeout = httpx.Timeout(timeout_seconds)

    async def status(self) -> dict:
        return await self._request("GET", "/v1/status")

    async def restart(
        self,
        component: SupervisorComponent,
        *,
        actor: str = "mcp",
        correlation_id: str | None = None,
    ) -> dict:
        if component not in {"api", "web", "mcp_tunnel", "all"}:
            raise ValueError("component must be api, web, mcp_tunnel, or all")
        token = self._resolve_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-SceneWorks-Actor": actor,
        }
        if correlation_id:
            headers["X-SceneWorks-Correlation-Id"] = str(correlation_id)[:120]
        if component == "all":
            return await self._request(
                "POST",
                "/v1/actions/restart-all",
                headers=headers,
                json_body={},
            )
        return await self._request(
            "POST",
            "/v1/actions/restart",
            headers=headers,
            json_body={"component": component},
        )

    def _resolve_token(self) -> str:
        token = self._token or os.environ.get("SCENEWORKS_SUPERVISOR_TOKEN", "").strip()
        if token:
            return token
        try:
            token = self._token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SupervisorUnavailable("local supervisor token is unavailable") from exc
        if not token:
            raise SupervisorUnavailable("local supervisor token is unavailable")
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                transport=self._transport,
                timeout=self._timeout,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    headers=headers,
                    json=json_body,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SupervisorUnavailable("local lifecycle supervisor is unavailable") from exc
        if not isinstance(payload, dict):
            raise SupervisorUnavailable("invalid lifecycle supervisor response")
        return payload


__all__ = ["SupervisorClient", "SupervisorComponent", "SupervisorUnavailable"]
