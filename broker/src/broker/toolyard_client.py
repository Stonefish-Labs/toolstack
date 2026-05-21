"""Client for the local toolyardd control socket."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class ToolyardControlError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"toolyard control failed: HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ToolyardControlClient:
    def __init__(self, socket_path: Path, client: httpx.AsyncClient | None = None):
        self.socket_path = socket_path
        self._client = client or httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
            base_url="http://toolyard",
            timeout=60.0,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_tools(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/tools")

    async def control_tool(self, tool_id: str, action: str) -> dict[str, Any]:
        return await self._request("POST", f"/v1/tools/{tool_id}/{action}")

    async def logs(self, tool_id: str, *, tail: int = 100) -> dict[str, Any]:
        return await self._request("GET", f"/v1/tools/{tool_id}/logs", params={"tail": tail})

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolyardControlError(
                503,
                f"toolyard control socket unavailable at {self.socket_path}",
            ) from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ToolyardControlError(response.status_code, str(detail)[:500])
        return response.json()
