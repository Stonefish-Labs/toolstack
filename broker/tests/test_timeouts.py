"""Tests for timeouts.py — expiration of pending requests."""

from __future__ import annotations

import time

import pytest

from broker import db
from broker.timeouts import expire_pending_requests
from broker.models import RequestStatus


@pytest.mark.asyncio
async def test_expire_pending_requests(tmp_db):
    caller = db.create_caller(tmp_db, "agent.exp")
    now = int(time.time())

    # Not yet expired
    db.create_request(
        tmp_db,
        caller_id=caller["id"],
        tool="t", op="a", args_json="{}",
        reason=None, status="pending_review",
        policy_decision="{}",
        expires_at=now + 3600,
    )

    # Already expired
    db.create_request(
        tmp_db,
        caller_id=caller["id"],
        tool="t", op="b", args_json="{}",
        reason=None, status="pending_review",
        policy_decision="{}",
        expires_at=now - 10,
    )

    count = await expire_pending_requests(tmp_db, now=now)
    assert count == 1

    # Verify the expired one changed status
    rows = db.list_requests(tmp_db, status="expired")
    assert len(rows) == 1
    assert rows[0]["op"] == "b"

    # Verify the non-expired one is still pending
    pending = db.list_requests(tmp_db, status="pending_review")
    assert len(pending) == 1
    assert pending[0]["op"] == "a"


@pytest.mark.asyncio
async def test_expire_nothing_when_none_pending(tmp_db):
    now = int(time.time())
    count = await expire_pending_requests(tmp_db, now=now)
    assert count == 0


@pytest.mark.asyncio
async def test_already_expired_not_double_counted(tmp_db):
    caller = db.create_caller(tmp_db, "agent.de")
    now = int(time.time())

    db.create_request(
        tmp_db,
        caller_id=caller["id"],
        tool="t", op="a", args_json="{}",
        reason=None, status="pending_review",
        policy_decision="{}",
        expires_at=now - 100,
    )

    # Expire once
    count1 = await expire_pending_requests(tmp_db, now=now)
    assert count1 == 1

    # Expire again — should be 0 (already expired)
    count2 = await expire_pending_requests(tmp_db, now=now)
    assert count2 == 0
