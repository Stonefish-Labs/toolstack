"""HTTP client for broker admin APIs."""

from __future__ import annotations

from typing import Any

import httpx

from broker_panel.config import Config


class BrokerAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"broker API failed: HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class BrokerClient:
    def __init__(self, config: Config, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=15)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_tools(self) -> dict[str, Any]:
        return (await self._request("GET", "/v1/admin/tools"))["tools"]

    async def reload_tools(self) -> dict[str, Any]:
        return await self._request("POST", "/v1/admin/tools/reload")

    async def get_toolyard_tools(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/v1/admin/toolyard/tools"))["tools"]

    async def control_toolyard_tool(self, tool_id: str, action: str) -> dict[str, Any]:
        return await self._request("POST", f"/v1/admin/toolyard/tools/{tool_id}/{action}")

    async def get_callers(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/v1/admin/callers"))["callers"]

    async def create_caller(
        self,
        name: str,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if policy is not None:
            body["policy"] = policy
        return await self._request("POST", "/v1/admin/callers", json=body)

    async def revoke_caller(self, name: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/admin/callers/{name}")

    async def get_tokens(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/v1/admin/tokens"))["tokens"]

    async def revoke_token(self, hash_prefix: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/admin/tokens/{hash_prefix}")

    async def get_caller_policy(self, caller: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/admin/callers/{caller}/policy")

    async def put_caller_policy(self, caller: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/v1/admin/callers/{caller}/policy", json=body)

    async def refresh_caller_token(self, caller: str) -> dict[str, Any]:
        return await self._request("POST", f"/v1/admin/callers/{caller}/refresh-token")

    async def get_requests(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/v1/requests?limit=10"))["requests"]

    async def get_audit(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/v1/audit?limit=10"))["events"]

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.config.broker_token()}"
        response = await self._client.request(
            method,
            f"{self.config.broker_url.rstrip('/')}{path}",
            headers=headers,
            **kwargs,
        )
        if response.status_code >= 400:
            raise BrokerAPIError(response.status_code, response.text[:500])
        return response.json()
