"""Tests for approval.py — approve/reject with terminal-state guards."""

from __future__ import annotations

import json
import time

import pytest

from broker import db
from broker.approval import approve_request, reject_request
from broker.dispatch import SyntheticDispatcher
from broker.lifecycle import handle_action_request
from broker.models import Caller, RequestStatus
from tests.conftest import create_test_caller


async def _create_pending_request(tmp_db, test_config):
    """Helper: create a pending_review request and return (caller, request)."""
    caller_row = create_test_caller(tmp_db, "agent.test", "tasks-agent")
    caller = Caller(**caller_row)
    dispatcher = SyntheticDispatcher()

    req, _ = await handle_action_request(
        caller=caller,
        tool="tasks",
        op="delete_object",
        arguments={"type": "task", "id": "t1"},
        reason="test",
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )
    assert req.status == RequestStatus.PENDING_REVIEW
    return caller, req


@pytest.mark.asyncio
async def test_approve_pending_request(tmp_db, test_config):
    _, req = await _create_pending_request(tmp_db, test_config)
    dispatcher = SyntheticDispatcher()

    result = await approve_request(
        request_id=req.id,
        approver="human",
        note="looks good",
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    assert result is not None
    assert result.status == RequestStatus.COMPLETED
    assert result.approver == "human"
    assert result.decision_note == "looks good"


@pytest.mark.asyncio
async def test_reject_pending_request(tmp_db, test_config):
    _, req = await _create_pending_request(tmp_db, test_config)

    result = await reject_request(
        request_id=req.id,
        approver="human",
        reason="not now",
        conn=tmp_db,
    )

    assert result is not None
    assert result.status == RequestStatus.REJECTED
    assert result.approver == "human"
    assert result.decision_note == "not now"


@pytest.mark.asyncio
async def test_approve_already_completed_is_noop(tmp_db, test_config):
    _, req = await _create_pending_request(tmp_db, test_config)
    dispatcher = SyntheticDispatcher()

    # Approve once
    await approve_request(
        request_id=req.id, approver="human", note=None,
        conn=tmp_db, dispatcher=dispatcher, config=test_config,
    )

    # Approve again — should be no-op
    result = await approve_request(
        request_id=req.id, approver="human2", note="again",
        conn=tmp_db, dispatcher=dispatcher, config=test_config,
    )
    assert result.status == RequestStatus.COMPLETED
    assert result.approver == "human"  # First approver preserved


@pytest.mark.asyncio
async def test_reject_already_rejected_is_noop(tmp_db, test_config):
    _, req = await _create_pending_request(tmp_db, test_config)

    await reject_request(
        request_id=req.id, approver="human", reason="no",
        conn=tmp_db,
    )

    result = await reject_request(
        request_id=req.id, approver="human2", reason="still no",
        conn=tmp_db,
    )
    assert result.status == RequestStatus.REJECTED


@pytest.mark.asyncio
async def test_approve_expired_request_fails(tmp_db):
    """Cannot approve a request that has already expired."""
    caller_row = create_test_caller(tmp_db, "agent.test", "tasks-agent")
    now = int(time.time())

    # Create a request that expired in the past
    row = db.create_request(
        tmp_db,
        caller_id=caller_row["id"],
        tool="tasks", op="create_task",
        args_json="{}",
        reason=None,
        status="pending_review",
        policy_decision='{"effect": "review", "reason": "test", "risk": "write", "grant_ttl_seconds": 3600}',
        expires_at=now - 10,
    )

    from broker.config import Config
    config = Config(approval_timeout_seconds=86400, allow_unknown_tools=True)
    dispatcher = SyntheticDispatcher()

    result = await approve_request(
        request_id=row["id"],
        approver="human",
        note="too late",
        conn=tmp_db,
        dispatcher=dispatcher,
        config=config,
    )

    assert result.status == RequestStatus.EXPIRED


@pytest.mark.asyncio
async def test_approve_nonexistent_returns_none(tmp_db, test_config):
    dispatcher = SyntheticDispatcher()
    result = await approve_request(
        request_id=99999,
        approver="human",
        note=None,
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )
    assert result is None


@pytest.mark.asyncio
async def test_approve_creates_grant(tmp_db, test_config):
    _, req = await _create_pending_request(tmp_db, test_config)
    dispatcher = SyntheticDispatcher()

    await approve_request(
        request_id=req.id,
        approver="human",
        note=None,
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    # Should have created a grant
    now = int(time.time())
    grants = db.find_active_grants(tmp_db, req.caller_id, "tasks", "delete_object", now)
    assert len(grants) >= 1
