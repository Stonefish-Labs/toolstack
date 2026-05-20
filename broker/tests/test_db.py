"""Tests for db.py — CRUD primitives against a real temp SQLite DB."""

from __future__ import annotations

import json
import time

from broker import db


def test_create_and_get_caller(tmp_db):
    row = db.create_caller(tmp_db, "agent.test", "home-default")
    assert row["name"] == "agent.test"
    assert row["profile"] == "home-default"
    assert row["revoked_at"] is None

    fetched = db.get_caller_by_id(tmp_db, row["id"])
    assert fetched["name"] == "agent.test"

    fetched = db.get_caller_by_name(tmp_db, "agent.test")
    assert fetched["id"] == row["id"]


def test_revoke_caller(tmp_db):
    db.create_caller(tmp_db, "agent.rev", "home-default")
    assert db.revoke_caller(tmp_db, "agent.rev")
    caller = db.get_caller_by_name(tmp_db, "agent.rev")
    assert caller["revoked_at"] is not None

    # Second revoke is no-op
    assert not db.revoke_caller(tmp_db, "agent.rev")


def test_list_callers_filters_revoked(tmp_db):
    db.create_caller(tmp_db, "active", "p")
    db.create_caller(tmp_db, "revoked", "p")
    db.revoke_caller(tmp_db, "revoked")

    active = db.list_callers(tmp_db, include_revoked=False)
    assert len(active) == 1
    assert active[0]["name"] == "active"

    all_callers = db.list_callers(tmp_db, include_revoked=True)
    assert len(all_callers) == 2


def test_token_crud(tmp_db):
    caller = db.create_caller(tmp_db, "agent.tok", "p")
    tok = db.create_token(tmp_db, caller["id"], "abc123hash")
    assert tok["token_hash"] == "abc123hash"
    assert tok["revoked_at"] is None

    fetched = db.get_token(tmp_db, "abc123hash")
    assert fetched is not None
    assert fetched["caller_id"] == caller["id"]


def test_revoke_token_by_prefix(tmp_db):
    caller = db.create_caller(tmp_db, "agent.rp", "p")
    db.create_token(tmp_db, caller["id"], "deadbeef1234")
    count = db.revoke_token(tmp_db, "deadbeef")
    assert count == 1

    tok = db.get_token(tmp_db, "deadbeef1234")
    assert tok["revoked_at"] is not None


def test_request_crud(tmp_db):
    caller = db.create_caller(tmp_db, "agent.req", "p")
    row = db.create_request(
        tmp_db,
        caller_id=caller["id"],
        tool="media",
        op="get_state",
        args_json='{"a": 1}',
        reason="test",
        status="pending_review",
        policy_decision='{"effect": "review"}',
        expires_at=int(time.time()) + 3600,
    )
    assert row["status"] == "pending_review"
    assert row["tool"] == "media"

    fetched = db.get_request(tmp_db, row["id"])
    assert fetched is not None

    # Update status
    updated = db.update_request_status(
        tmp_db, row["id"],
        status="completed",
        result_json='{"ok": true}',
    )
    assert updated["status"] == "completed"
    assert updated["result_json"] == '{"ok": true}'


def test_list_requests_with_filters(tmp_db):
    caller = db.create_caller(tmp_db, "agent.lr", "p")
    for i in range(5):
        db.create_request(
            tmp_db,
            caller_id=caller["id"],
            tool="t",
            op=f"op{i}",
            args_json="{}",
            reason=None,
            status="pending_review" if i < 3 else "completed",
            policy_decision="{}",
        )

    pending = db.list_requests(tmp_db, status="pending_review")
    assert len(pending) == 3

    limited = db.list_requests(tmp_db, limit=2)
    assert len(limited) == 2

    after = db.list_requests(tmp_db, after_id=3)
    assert all(r["id"] > 3 for r in after)


def test_find_expired_pending(tmp_db):
    caller = db.create_caller(tmp_db, "agent.exp", "p")
    now = int(time.time())

    # Not expired
    db.create_request(
        tmp_db,
        caller_id=caller["id"],
        tool="t", op="a",
        args_json="{}",
        reason=None,
        status="pending_review",
        policy_decision="{}",
        expires_at=now + 3600,
    )
    # Expired
    db.create_request(
        tmp_db,
        caller_id=caller["id"],
        tool="t", op="b",
        args_json="{}",
        reason=None,
        status="pending_review",
        policy_decision="{}",
        expires_at=now - 10,
    )

    expired = db.find_expired_pending(tmp_db, now)
    assert len(expired) == 1
    assert expired[0]["op"] == "b"


def test_approval_crud(tmp_db):
    caller = db.create_caller(tmp_db, "agent.ap", "p")
    req = db.create_request(
        tmp_db,
        caller_id=caller["id"],
        tool="t", op="a", args_json="{}",
        reason=None, status="pending_review", policy_decision="{}",
    )
    approval = db.record_approval(tmp_db, req["id"], "human", "approve", "lgtm")
    assert approval["approver"] == "human"
    assert approval["action"] == "approve"

    approvals = db.list_approvals_for_request(tmp_db, req["id"])
    assert len(approvals) == 1


def test_grant_crud(tmp_db):
    caller = db.create_caller(tmp_db, "agent.gr", "p")
    now = int(time.time())

    grant = db.create_grant(tmp_db, caller["id"], "media", "skip", now + 3600)
    assert grant["tool"] == "media"

    active = db.find_active_grants(tmp_db, caller["id"], "media", "skip", now)
    assert len(active) == 1

    # No match for wrong op
    empty = db.find_active_grants(tmp_db, caller["id"], "media", "play", now)
    assert len(empty) == 0


def test_audit_crud(tmp_db):
    event = db.record_audit(
        tmp_db, "request.created",
        request_id=1, caller_id=1, tool="t", op="a",
        detail={"key": "val"},
    )
    assert event["kind"] == "request.created"
    assert json.loads(event["detail_json"]) == {"key": "val"}

    events = db.list_audit_events(tmp_db, limit=10)
    assert len(events) == 1
