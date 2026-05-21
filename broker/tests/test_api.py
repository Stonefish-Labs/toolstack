"""Tests for api.py — FastAPI routes via TestClient."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from broker import db, policy, tokens
from broker.api import (
    JSONRPC_DENIED,
    JSONRPC_PENDING_REVIEW,
    JSONRPC_UNKNOWN_TOOL,
    JSONRPC_TOOL_UNREACHABLE,
    create_app,
)
from broker.config import Config
from broker.dispatch import SyntheticDispatcher
from broker.signing import make_signature_headers
from tests.conftest import create_test_caller


@pytest.fixture
def app_client(tmp_path):
    """Create a test app with TestClient."""
    config = Config(
        bind_addr="127.0.0.1:0",
        state_dir=tmp_path / "state",
        tools_dir=tmp_path / "tools",
        approval_timeout_seconds=86400,
        grant_default_ttl_seconds=3600,
        allow_unknown_tools=True,
    )
    (tmp_path / "tools").mkdir(exist_ok=True)
    app = create_app(config, SyntheticDispatcher())

    with TestClient(app) as client:
        yield client, config


@pytest.fixture
def agent_token(app_client):
    """Create an agent caller and return (token, client)."""
    client, config = app_client
    from broker.api import _conn
    caller = create_test_caller(_conn, "agent.test", "home-default")
    raw, _ = tokens.create_token_for_caller(_conn, caller["id"])
    return raw, client


@pytest.fixture
def approver_token(app_client):
    """Create an approver caller and return (token, client)."""
    client, config = app_client
    from broker.api import _conn
    caller = create_test_caller(_conn, "bot.approver", "approver")
    raw, _ = tokens.create_token_for_caller(_conn, caller["id"])
    return raw, client


@pytest.fixture
def registry_admin_token(app_client):
    """Create a registry-admin caller and return (token, client)."""
    client, config = app_client
    from broker.api import _conn
    caller = create_test_caller(_conn, "svc.toolyard", "registry-admin")
    raw, _ = tokens.create_token_for_caller(_conn, caller["id"])
    return raw, client


@pytest.fixture
def admin_app_client(tmp_path):
    """Create a test app with registered tools for admin policy editing."""
    tools_dir = tmp_path / "tools"
    (tools_dir / "music").mkdir(parents=True)
    (tools_dir / "music" / "toolyard.yaml").write_text(
        """
id: music
type: rest
entrypoint:
  image: music:latest
  port: 5200
operations:
  - { op: get_status, risk: read }
  - { op: play_item, risk: write }
""",
        encoding="utf-8",
    )
    config = Config(
        bind_addr="127.0.0.1:0",
        state_dir=tmp_path / "state",
        tools_dir=tools_dir,
        approval_timeout_seconds=86400,
        grant_default_ttl_seconds=3600,
        allow_unknown_tools=False,
    )
    app = create_app(config, SyntheticDispatcher())

    with TestClient(app) as client:
        yield client, config


@pytest.fixture
def admin_token(admin_app_client):
    client, _ = admin_app_client
    from broker.api import _conn
    caller = create_test_caller(_conn, "svc.panel", "control-panel-admin")
    raw, _ = tokens.create_token_for_caller(_conn, caller["id"])
    return raw, client



@pytest.fixture
def tasks_agent_token(app_client):
    """Create a Tasks read/write caller and return (token, client)."""
    client, config = app_client
    from broker.api import _conn
    caller = create_test_caller(_conn, "agent.tasks-test", "tasks-agent")
    raw, _ = tokens.create_token_for_caller(_conn, caller["id"])
    return raw, client


@pytest.fixture
def signed_app_client(tmp_path):
    """Create a test app that requires HMAC signatures for approver callers."""
    config = Config(
        bind_addr="127.0.0.1:0",
        state_dir=tmp_path / "state",
        tools_dir=tmp_path / "tools",
        approval_timeout_seconds=86400,
        grant_default_ttl_seconds=3600,
        allow_unknown_tools=True,
        approver_signing_secret="test-signing-secret",
    )
    (tmp_path / "tools").mkdir(exist_ok=True)
    app = create_app(config, SyntheticDispatcher())

    from broker.api import _signature_nonces
    _signature_nonces.clear()

    with TestClient(app) as client:
        yield client, config


@pytest.fixture
def signed_approver_token(signed_app_client):
    client, config = signed_app_client
    from broker.api import _conn
    caller = create_test_caller(_conn, "bot.signed", "approver")
    raw, _ = tokens.create_token_for_caller(_conn, caller["id"])
    return raw, client


def _signed_headers(
    token: str,
    method: str,
    target: str,
    body: bytes = b"",
    *,
    secret: str = "test-signing-secret",
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(
        make_signature_headers(
            secret, method, target, body, timestamp=timestamp, nonce=nonce
        )
    )
    return headers


def test_health(app_client):
    client, _ = app_client
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_health_no_auth_required(app_client):
    client, _ = app_client
    resp = client.get("/v1/health")
    assert resp.status_code == 200


def test_action_requires_auth(app_client):
    client, _ = app_client
    resp = client.post("/v1/actions/media.get_state", json={"arguments": {"type": "task", "id": "t1"}})
    assert resp.status_code == 422 or resp.status_code == 401


def test_action_invalid_token(app_client):
    client, _ = app_client
    resp = client.post(
        "/v1/actions/media.get_state",
        json={"arguments": {"type": "task", "id": "t1"}},
        headers={"Authorization": "Bearer invalid"},
    )
    assert resp.status_code == 401


def test_action_allowed_completes(agent_token):
    raw, client = agent_token
    resp = client.post(
        "/v1/actions/hello-rest.greet",
        json={"arguments": {"name": "test"}, "reason": "test"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["result"]["synthetic"] is True


def test_action_review_returns_202(tasks_agent_token):
    raw, client = tasks_agent_token
    resp = client.post(
        "/v1/actions/tasks.delete_object",
        json={"arguments": {"type": "task", "id": "t1"}, "reason": "delete test"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending_review"
    assert "request_id" in data


def test_action_denied_returns_403(agent_token):
    raw, client = agent_token
    resp = client.post(
        "/v1/actions/admin.do_stuff",
        json={"arguments": {"type": "task", "id": "t1"}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


def test_list_requests_requires_broker_op(agent_token):
    """Agent policy doesn't have broker.list_requests -> 403."""
    raw, client = agent_token
    resp = client.get(
        "/v1/requests",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


def test_list_requests_with_approver(approver_token):
    raw, client = approver_token
    resp = client.get(
        "/v1/requests",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    assert "requests" in resp.json()


def test_admin_endpoints_require_admin_policy(agent_token):
    raw, client = agent_token
    resp = client.get(
        "/v1/admin/callers",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


def test_admin_create_caller_returns_one_time_token(admin_token):
    raw, client = admin_token
    resp = client.post(
        "/v1/admin/callers",
        json={
            "name": "agent.kira",
            "policy": {"tools": {"music": {"operations": {"get_status": "allow"}}}},
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["caller"]["name"] == "agent.kira"
    assert set(data["caller"]) == {"id", "name", "created_at", "revoked_at"}
    assert data["token"]
    assert len(data["hash_prefix"]) == 8

    resp = client.get(
        "/v1/admin/tokens",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    token_rows = resp.json()["tokens"]
    assert any(row["caller_name"] == "agent.kira" for row in token_rows)
    assert all("token_hash" not in row for row in token_rows)


def test_admin_tools_include_declared_operation_risk(admin_token):
    raw, client = admin_token
    resp = client.get(
        "/v1/admin/tools",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    music = resp.json()["tools"]["music"]
    assert music["operations"] == [
        {"op": "get_status", "risk": "read", "description": ""},
        {"op": "play_item", "risk": "write", "description": ""},
    ]


def test_admin_tools_reload_uses_admin_policy(admin_token, admin_app_client):
    raw, client = admin_token
    _, config = admin_app_client
    notes_dir = config.tools_dir / "notes"
    notes_dir.mkdir()
    (notes_dir / "toolyard.yaml").write_text(
        """
id: notes
type: rest
entrypoint:
  image: notes:latest
  port: 5300
operations:
  - { op: list_notes, risk: read }
""",
        encoding="utf-8",
    )

    resp = client.post(
        "/v1/admin/tools/reload",
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200
    assert resp.json()["tool_count"] == 2
    tools = client.get(
        "/v1/admin/tools",
        headers={"Authorization": f"Bearer {raw}"},
    ).json()["tools"]
    assert "notes" in tools


def test_admin_tools_reload_requires_admin_policy(agent_token):
    raw, client = agent_token
    resp = client.post(
        "/v1/admin/tools/reload",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


def test_admin_caller_policy_roundtrip_structured_operation_rules(admin_token):
    raw, client = admin_token
    create = client.post(
        "/v1/admin/callers",
        json={"name": "agent.panel-test"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert create.status_code == 200
    payload = {
        "tools": {
            "music": {
                "operations": {
                    "get_status": "allow",
                    "play_item": "review",
                }
            }
        },
        "auto_grant_ttl_seconds": 120,
    }
    resp = client.put(
        "/v1/admin/callers/agent.panel-test/policy",
        json=payload,
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["caller"] == "agent.panel-test"
    assert body["tools"]["music"]["operations"]["get_status"] == "allow"
    assert body["tools"]["music"]["operations"]["play_item"] == "review"
    assert body["auto_grant_ttl_seconds"] == 120


def test_approve_reject_flow(app_client):
    """End-to-end: agent creates pending request, approver approves it."""
    client, config = app_client
    from broker.api import _conn

    # Create both callers
    agent = create_test_caller(_conn, "agent.e2e", "tasks-agent")
    agent_raw, _ = tokens.create_token_for_caller(_conn, agent["id"])
    approver = create_test_caller(_conn, "bot.e2e", "approver")
    approver_raw, _ = tokens.create_token_for_caller(_conn, approver["id"])

    # Agent: create a review-required request
    resp = client.post(
        "/v1/actions/tasks.delete_object",
        json={"arguments": {"type": "task", "id": "t1"}, "reason": "e2e test"},
        headers={"Authorization": f"Bearer {agent_raw}"},
    )
    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    # Approver: list pending
    resp = client.get(
        "/v1/requests?status=pending_review",
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    requests = resp.json()["requests"]
    assert any(r["id"] == request_id for r in requests)

    # Approver: get single request
    resp = client.get(
        f"/v1/requests/{request_id}",
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    req_data = resp.json()
    assert req_data["caller"] == "agent.e2e"
    assert req_data["tool"] == "tasks"
    assert req_data["op"] == "delete_object"
    assert req_data["status"] == "pending_review"

    # Approver: approve
    resp = client.post(
        f"/v1/requests/{request_id}/approve",
        json={"approver": "testuser", "note": "lgtm"},
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    approved = resp.json()
    assert approved["status"] == "completed"
    assert approved["approver"] == "testuser"
    assert approved["decision_note"] == "lgtm"


def test_reject_flow(app_client):
    client, config = app_client
    from broker.api import _conn

    agent = create_test_caller(_conn, "agent.rej", "tasks-agent")
    agent_raw, _ = tokens.create_token_for_caller(_conn, agent["id"])
    approver = create_test_caller(_conn, "bot.rej", "approver")
    approver_raw, _ = tokens.create_token_for_caller(_conn, approver["id"])

    # Create pending request
    resp = client.post(
        "/v1/actions/tasks.delete_object",
        json={"arguments": {"type": "task", "id": "t1"}},
        headers={"Authorization": f"Bearer {agent_raw}"},
    )
    request_id = resp.json()["request_id"]

    # Reject
    resp = client.post(
        f"/v1/requests/{request_id}/reject",
        json={"approver": "testuser", "reason": "not allowed"},
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    rejected = resp.json()
    assert rejected["status"] == "rejected"
    assert rejected["decision_note"] == "not allowed"


def test_approval_message_endpoints_with_approver(app_client):
    client, config = app_client
    from broker.api import _conn

    agent = create_test_caller(_conn, "agent.msg", "tasks-agent")
    approver = create_test_caller(_conn, "bot.msg", "approver")
    approver_raw, _ = tokens.create_token_for_caller(_conn, approver["id"])
    req = db.create_request(
        _conn,
        caller_id=agent["id"],
        tool="tasks",
        op="delete_object",
        args_json="{}",
        reason=None,
        status="pending_review",
        policy_decision="{}",
    )

    resp = client.get(
        f"/v1/approval-messages/{req['id']}",
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 404

    resp = client.put(
        f"/v1/approval-messages/{req['id']}",
        json={"message_id": 999, "last_status": "pending_review"},
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == req["id"]
    assert data["surface"] == "discord"
    assert data["message_id"] == 999

    resp = client.get(
        "/v1/approval-messages",
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    assert [m["request_id"] for m in resp.json()["messages"]] == [req["id"]]

    resp = client.put(
        f"/v1/approval-messages/{req['id']}",
        json={"message_id": 999, "last_status": "completed"},
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    assert resp.json()["last_status"] == "completed"

    resp = client.delete(
        f"/v1/approval-messages/{req['id']}",
        headers={"Authorization": f"Bearer {approver_raw}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_approval_message_endpoints_require_approver_ops(agent_token):
    raw, client = agent_token
    resp = client.get(
        "/v1/approval-messages",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


def test_approval_message_upsert_requires_existing_request(approver_token):
    raw, client = approver_token
    resp = client.put(
        "/v1/approval-messages/99999",
        json={"message_id": 999, "last_status": "pending_review"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404


def test_audit_endpoint(approver_token):
    raw, client = approver_token
    resp = client.get(
        "/v1/audit",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    assert "events" in resp.json()


def test_registry_endpoint(agent_token):
    raw, client = agent_token
    resp = client.get(
        "/v1/registry",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    assert "tools" in resp.json()


def test_request_not_found(approver_token):
    raw, client = approver_token
    resp = client.get(
        "/v1/requests/99999",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404


# ── Registry reload auth ────────────────────────────────────────────


def test_reload_requires_broker_op(agent_token):
    """Agents without broker.registry.reload should get 403."""
    raw, client = agent_token
    resp = client.post(
        "/v1/registry/reload",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


def test_reload_forbidden_for_approver(approver_token):
    """The approver policy cannot reload the broker registry."""
    raw, client = approver_token
    resp = client.post(
        "/v1/registry/reload",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


def test_reload_allowed_for_registry_admin(registry_admin_token):
    """The registry-admin policy owns broker.registry.reload."""
    raw, client = registry_admin_token
    resp = client.post(
        "/v1/registry/reload",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    assert resp.json()["reloaded"] is True


def test_reload_unauthenticated(app_client):
    """No bearer at all → 401."""
    client, _ = app_client
    resp = client.post("/v1/registry/reload")
    assert resp.status_code in (401, 422)


# ── Approver request signing ────────────────────────────────────────


def test_signed_approver_get_and_approve_succeed(signed_app_client):
    client, _ = signed_app_client
    from broker.api import _conn

    agent = create_test_caller(_conn, "agent.signing", "tasks-agent")
    agent_raw, _ = tokens.create_token_for_caller(_conn, agent["id"])
    approver = create_test_caller(_conn, "bot.signing", "approver")
    approver_raw, _ = tokens.create_token_for_caller(_conn, approver["id"])

    resp = client.post(
        "/v1/actions/tasks.delete_object",
        json={"arguments": {"type": "task", "id": "t1"}, "reason": "signed approval test"},
        headers={"Authorization": f"Bearer {agent_raw}"},
    )
    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    resp = client.get(
        "/v1/requests?status=pending_review",
        headers=_signed_headers(
            approver_raw, "GET", "/v1/requests?status=pending_review"
        ),
    )
    assert resp.status_code == 200

    body = b'{"approver":"testuser","note":"ok"}'
    headers = _signed_headers(
        approver_raw, "POST", f"/v1/requests/{request_id}/approve", body
    )
    headers["Content-Type"] = "application/json"
    resp = client.post(
        f"/v1/requests/{request_id}/approve", content=body, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_unsigned_approver_rejected_when_signing_configured(signed_approver_token):
    raw, client = signed_approver_token
    resp = client.get("/v1/requests", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401


def test_bad_approver_signature_rejected(signed_approver_token):
    raw, client = signed_approver_token
    resp = client.get(
        "/v1/requests",
        headers=_signed_headers(raw, "GET", "/v1/requests", secret="wrong-secret"),
    )
    assert resp.status_code == 401


def test_stale_approver_signature_rejected(signed_approver_token):
    raw, client = signed_approver_token
    resp = client.get(
        "/v1/requests",
        headers=_signed_headers(raw, "GET", "/v1/requests", timestamp="1"),
    )
    assert resp.status_code == 401


def test_reused_approver_nonce_rejected(signed_approver_token):
    raw, client = signed_approver_token
    headers = _signed_headers(
        raw,
        "GET",
        "/v1/requests",
        timestamp=str(int(time.time())),
        nonce="fixed-test-nonce",
    )
    assert client.get("/v1/requests", headers=headers).status_code == 200
    assert client.get("/v1/requests", headers=headers).status_code == 401


def test_approver_body_tampering_rejected(signed_app_client):
    client, _ = signed_app_client
    from broker.api import _conn

    agent = create_test_caller(_conn, "agent.tamper", "tasks-agent")
    agent_raw, _ = tokens.create_token_for_caller(_conn, agent["id"])
    approver = create_test_caller(_conn, "bot.tamper", "approver")
    approver_raw, _ = tokens.create_token_for_caller(_conn, approver["id"])

    resp = client.post(
        "/v1/actions/tasks.delete_object",
        json={"arguments": {"type": "task", "id": "t1"}},
        headers={"Authorization": f"Bearer {agent_raw}"},
    )
    request_id = resp.json()["request_id"]

    signed_body = b'{"approver":"testuser","note":"ok"}'
    sent_body = b'{"approver":"testuser","note":"tampered"}'
    headers = _signed_headers(
        approver_raw, "POST", f"/v1/requests/{request_id}/approve", signed_body
    )
    headers["Content-Type"] = "application/json"
    resp = client.post(
        f"/v1/requests/{request_id}/approve", content=sent_body, headers=headers
    )
    assert resp.status_code == 401


# ── /mcp/<tool> blind forwarder ────────────────────────────────────


def _write_mcp_tool(tools_dir, tool_id="time-mcp", port=5100, ttype="mcp-http"):
    """Write a minimal mcp-http toolyard.yaml fixture."""
    tool_dir = tools_dir / tool_id
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "toolyard.yaml").write_text(f"""
id: {tool_id}
type: {ttype}
entrypoint:
  build: .
  port: {port}
operations:
  - {{ op: current_time, risk: read }}
  - {{ op: skip_dance, risk: write }}
""")


@pytest.fixture
def mcp_app_client(tmp_path):
    """App with a registered time-mcp tool and caller policy presets."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    _write_mcp_tool(tools_dir)

    config = Config(
        bind_addr="127.0.0.1:0",
        state_dir=tmp_path / "state",
        tools_dir=tools_dir,
        approval_timeout_seconds=86400,
        grant_default_ttl_seconds=0,
        allow_unknown_tools=False,
    )
    app = create_app(config, SyntheticDispatcher())
    with TestClient(app) as client:
        yield client, config


def _make_caller(client, name, policy_name):
    """Create a caller via direct DB call and return its raw token."""
    from broker.api import _conn
    caller = create_test_caller(_conn, name, policy_name)
    raw, _ = tokens.create_token_for_caller(_conn, caller["id"])
    return raw


def test_mcp_requires_auth(mcp_app_client):
    client, _ = mcp_app_client
    resp = client.post(
        "/mcp/time-mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    assert resp.status_code in (401, 422)


def test_mcp_invalid_bearer(mcp_app_client):
    client, _ = mcp_app_client
    resp = client.post(
        "/mcp/time-mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": "Bearer bogus"},
    )
    assert resp.status_code == 401


def test_mcp_unknown_tool(mcp_app_client):
    client, _ = mcp_app_client
    raw = _make_caller(client, "agent.tester", "mcp-tester")
    resp = client.post(
        "/mcp/no-such-tool",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == JSONRPC_UNKNOWN_TOOL


def test_mcp_non_mcp_tool_rejected(mcp_app_client, tmp_path):
    """Forwarding to a REST tool via /mcp/<tool> is rejected."""
    client, config = mcp_app_client
    # Add a REST tool to the same registry, then reload.
    _write_mcp_tool(config.tools_dir, tool_id="hello-rest", port=5000, ttype="rest")
    from broker import registry
    registry.reload()

    raw = _make_caller(client, "agent.rest", "mcp-tester")
    resp = client.post(
        "/mcp/hello-rest",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 400
    assert "not an MCP tool" in resp.json()["error"]["message"]


def test_mcp_tools_list_blind_forwards(mcp_app_client, monkeypatch):
    """tools/list goes through _blind_forward_mcp with the shared http client."""
    client, _ = mcp_app_client
    raw = _make_caller(client, "agent.list", "mcp-tester")

    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": 99,
            "result": {"tools": [{"name": "current_time"}]},
        })

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("broker.api._http_client", mock_client)

    resp = client.post(
        "/mcp/time-mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 99},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["tools"][0]["name"] == "current_time"
    # Verify the broker forwarded to the right place
    assert "/mcp" in captured["url"]
    assert captured["body"]["method"] == "tools/list"


def test_mcp_tools_list_denied_for_caller_without_tool(mcp_app_client):
    """A caller policy that doesn't include time-mcp can't list its tools."""
    client, _ = mcp_app_client
    raw = _make_caller(client, "agent.nomcp", "no-mcp")
    resp = client.post(
        "/mcp/time-mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == JSONRPC_DENIED


def test_mcp_tools_call_allowed_completes(mcp_app_client):
    """tools/call for an allowed op runs through the dispatcher and returns the result."""
    client, _ = mcp_app_client
    raw = _make_caller(client, "agent.callok", "mcp-tester")
    resp = client.post(
        "/mcp/time-mcp",
        json={
            "jsonrpc": "2.0", "id": 7,
            "method": "tools/call",
            "params": {"name": "current_time", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 7
    assert "result" in body
    # SyntheticDispatcher returns {synthetic: True, tool: ..., op: ..., ...}
    assert body["result"]["synthetic"] is True
    assert body["result"]["op"] == "current_time"


def test_mcp_tools_call_review_returns_jsonrpc_error(mcp_app_client):
    """tools/call for a review-required op returns -32000 with request_id."""
    client, _ = mcp_app_client
    raw = _make_caller(client, "agent.review", "mcp-tester")
    resp = client.post(
        "/mcp/time-mcp",
        json={
            "jsonrpc": "2.0", "id": 8,
            "method": "tools/call",
            "params": {"name": "skip_dance", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["error"]["code"] == JSONRPC_PENDING_REVIEW
    assert "request_id" in body["error"]["data"]
    assert body["error"]["data"]["status"] == "pending_review"


def test_mcp_tools_call_denied(mcp_app_client):
    """A caller policy that doesn't allow the op gets -32001 denied."""
    client, _ = mcp_app_client
    raw = _make_caller(client, "agent.denied", "no-mcp")
    resp = client.post(
        "/mcp/time-mcp",
        json={
            "jsonrpc": "2.0", "id": 9,
            "method": "tools/call",
            "params": {"name": "current_time", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == JSONRPC_DENIED


def test_mcp_tools_call_missing_name(mcp_app_client):
    """tools/call without params.name is rejected as invalid frame."""
    client, _ = mcp_app_client
    raw = _make_caller(client, "agent.bad", "mcp-tester")
    resp = client.post(
        "/mcp/time-mcp",
        json={
            "jsonrpc": "2.0", "id": 10,
            "method": "tools/call",
            "params": {},
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    body = resp.json()
    assert body["error"]["code"] == -32600  # invalid frame


def test_mcp_stdio_not_yet_supported(mcp_app_client, tmp_path):
    """mcp-stdio tools return a clean 'not yet supported' error."""
    client, config = mcp_app_client
    _write_mcp_tool(config.tools_dir, tool_id="legacy-mcp", port=5200, ttype="mcp-stdio")
    from broker import registry
    registry.reload()

    raw = _make_caller(client, "agent.legacy", "mcp-tester")
    resp = client.post(
        "/mcp/legacy-mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 501
    assert "mcp-stdio" in resp.json()["error"]["message"]
