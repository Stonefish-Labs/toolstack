"""Broker client protocol and implementations.

BrokerClient is the protocol that the reconciler uses to talk to the broker.
Implementations:
  - HTTPBrokerClient: real HTTP client using httpx
  - MockBrokerClient: in-memory, for tests
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx

from discord_approver.models import Request, RequestStatus
from discord_approver.signing import make_signature_headers

logger = logging.getLogger(__name__)


@runtime_checkable
class BrokerClient(Protocol):
    """Protocol for communicating with the broker's approval endpoints."""

    async def list_pending(self, after_id: int | None = None) -> list[Request]:
        """Fetch pending_review requests, optionally after a given ID."""
        ...

    async def get_request(self, request_id: int) -> Request | None:
        """Fetch a single request by ID. Returns None if not found."""
        ...

    async def approve(
        self, request_id: int, approver: str, note: str | None = None
    ) -> Request:
        """Approve a request. Returns the updated request."""
        ...

    async def reject(
        self, request_id: int, approver: str, reason: str | None = None
    ) -> Request:
        """Reject a request. Returns the updated request."""
        ...


class HTTPBrokerClient:
    """Real broker client using httpx with retries and backoff."""

    MAX_RETRIES = 3
    BACKOFF_BASE = 1.0  # seconds

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        signing_secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._signing_secret = signing_secret
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request_with_retry(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff on 5xx and 429."""
        import asyncio

        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                request = self._client.build_request(method, path, **kwargs)
                signing_secret = getattr(self, "_signing_secret", None)
                if signing_secret:
                    request.headers.update(
                        make_signature_headers(
                            signing_secret,
                            method,
                            request.url.raw_path.decode("ascii"),
                            request.content,
                        )
                    )
                resp = await self._client.send(request)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "2"))
                    logger.warning(
                        "broker rate limited, retrying in %.1fs", retry_after
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status_code >= 500:
                    wait = self.BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "broker returned %d, retrying in %.1fs",
                        resp.status_code,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                return resp

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exc = e
                wait = self.BACKOFF_BASE * (2**attempt)
                logger.warning(
                    "broker unreachable (%s), retrying in %.1fs",
                    type(e).__name__,
                    wait,
                )
                await asyncio.sleep(wait)

        if last_exc:
            raise last_exc
        raise httpx.HTTPError(f"broker request failed after {self.MAX_RETRIES} retries")

    async def list_pending(self, after_id: int | None = None) -> list[Request]:
        params: dict = {"status": "pending_review"}
        if after_id is not None:
            params["after_id"] = after_id
        resp = await self._request_with_retry("GET", "/v1/requests", params=params)
        resp.raise_for_status()
        data = resp.json()
        # Handle both list and {"requests": [...]} shapes
        items = data if isinstance(data, list) else data.get("requests", [])
        return [Request.model_validate(item) for item in items]

    async def get_request(self, request_id: int) -> Request | None:
        resp = await self._request_with_retry("GET", f"/v1/requests/{request_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Request.model_validate(resp.json())

    async def approve(
        self, request_id: int, approver: str, note: str | None = None
    ) -> Request:
        body = {"approver": approver, "note": note}
        resp = await self._request_with_retry(
            "POST", f"/v1/requests/{request_id}/approve", json=body
        )
        resp.raise_for_status()
        return Request.model_validate(resp.json())

    async def reject(
        self, request_id: int, approver: str, reason: str | None = None
    ) -> Request:
        body = {"approver": approver, "reason": reason}
        resp = await self._request_with_retry(
            "POST", f"/v1/requests/{request_id}/reject", json=body
        )
        resp.raise_for_status()
        return Request.model_validate(resp.json())


class MockBrokerClient:
    """In-memory broker client for testing."""

    def __init__(self) -> None:
        self._requests: dict[int, Request] = {}
        self._next_id = 1

    def inject(self, **kwargs) -> Request:
        """Add a request to the in-memory store. Returns the created request."""
        req = Request(id=self._next_id, **kwargs)
        self._requests[req.id] = req
        self._next_id += 1
        return req

    async def list_pending(self, after_id: int | None = None) -> list[Request]:
        results = [
            r
            for r in self._requests.values()
            if r.status == RequestStatus.PENDING_REVIEW
            and (after_id is None or r.id > after_id)
        ]
        return sorted(results, key=lambda r: r.id)

    async def get_request(self, request_id: int) -> Request | None:
        return self._requests.get(request_id)

    async def approve(
        self, request_id: int, approver: str, note: str | None = None
    ) -> Request:
        req = self._requests[request_id]
        updated = req.model_copy(
            update={
                "status": RequestStatus.APPROVED,
                "approver": approver,
                "decision_note": note,
            }
        )
        self._requests[request_id] = updated
        return updated

    async def reject(
        self, request_id: int, approver: str, reason: str | None = None
    ) -> Request:
        req = self._requests[request_id]
        updated = req.model_copy(
            update={
                "status": RequestStatus.REJECTED,
                "approver": approver,
                "decision_note": reason,
            }
        )
        self._requests[request_id] = updated
        return updated
