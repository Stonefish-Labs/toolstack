"""Tests for the HTTPBrokerClient against the fake broker.

These tests start the fake broker in-process using httpx's ASGITransport
so no real server is needed.
"""

from __future__ import annotations

import pytest
import httpx

from discord_approver.broker_client import HTTPBrokerClient
from discord_approver.models import RequestStatus
from discord_approver.signing import SIGNATURE_HEADER, compute_signature


@pytest.fixture
def fake_app():
    """Import the fake broker FastAPI app."""
    import os
    os.environ.setdefault("FAKE_BROKER_TOKEN", "test-token")
    from discord_approver.scaffolding.fake_broker import app, store
    store.reset()
    return app, store


@pytest.fixture
async def client(fake_app):
    """Create an HTTPBrokerClient pointed at the fake broker via ASGITransport."""
    app, _ = fake_app
    transport = httpx.ASGITransport(app=app)
    broker = HTTPBrokerClient(
        "http://testserver", "test-token", transport=transport
    )
    yield broker
    await broker.close()


@pytest.fixture
def fake_store(fake_app):
    _, store = fake_app
    return store


class TestSigning:
    async def test_signed_get_includes_valid_hmac_headers(self):
        captured: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["target"] = request.url.raw_path.decode("ascii")
            captured["headers"] = dict(request.headers)
            captured["body"] = request.content
            return httpx.Response(200, json={"requests": []})

        broker = HTTPBrokerClient(
            "http://testserver",
            "test-token",
            signing_secret="secret",
            transport=httpx.MockTransport(handler),
        )
        try:
            await broker.list_pending()
        finally:
            await broker.close()

        headers = captured["headers"]
        target = captured["target"]
        body = captured["body"]
        assert target == "/v1/requests?status=pending_review"
        assert headers["x-toolstack-timestamp"]
        assert headers["x-toolstack-nonce"]
        signature = headers[SIGNATURE_HEADER.lower()].split("=", 1)[1]
        assert signature == compute_signature(
            "secret",
            "GET",
            target,
            headers["x-toolstack-timestamp"],
            headers["x-toolstack-nonce"],
            body,
        )


class TestListPending:
    async def test_empty(self, client):
        result = await client.list_pending()
        assert result == []

    async def test_returns_pending(self, client, fake_store):
        fake_store.inject({"tool": "media", "op": "play"})
        result = await client.list_pending()
        assert len(result) == 1
        assert result[0].tool == "media"
        assert result[0].status == RequestStatus.PENDING_REVIEW

    async def test_after_id_filters(self, client, fake_store):
        fake_store.inject({"tool": "media", "op": "play"})
        fake_store.inject({"tool": "media", "op": "skip"})
        result = await client.list_pending(after_id=1)
        assert len(result) == 1
        assert result[0].id == 2


class TestGetRequest:
    async def test_found(self, client, fake_store):
        fake_store.inject({"tool": "tasks", "op": "list"})
        result = await client.get_request(1)
        assert result is not None
        assert result.tool == "tasks"

    async def test_not_found(self, client):
        result = await client.get_request(999)
        assert result is None


class TestApprove:
    async def test_approve_succeeds(self, client, fake_store):
        fake_store.inject({"tool": "media", "op": "skip"})
        result = await client.approve(1, "testuser", "looks good")
        assert result.status == RequestStatus.APPROVED
        assert result.approver == "testuser"
        assert result.decision_note == "looks good"

    async def test_approve_without_note(self, client, fake_store):
        fake_store.inject({"tool": "media", "op": "skip"})
        result = await client.approve(1, "testuser")
        assert result.status == RequestStatus.APPROVED
        assert result.decision_note is None


class TestReject:
    async def test_reject_with_reason(self, client, fake_store):
        fake_store.inject({"tool": "media", "op": "delete"})
        result = await client.reject(1, "testuser", "too dangerous")
        assert result.status == RequestStatus.REJECTED
        assert result.approver == "testuser"
        assert result.decision_note == "too dangerous"

    async def test_reject_without_reason(self, client, fake_store):
        fake_store.inject({"tool": "media", "op": "delete"})
        result = await client.reject(1, "testuser")
        assert result.status == RequestStatus.REJECTED
        assert result.decision_note is None
