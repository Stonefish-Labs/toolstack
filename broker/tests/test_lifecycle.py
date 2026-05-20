"""Tests for lifecycle.py — request state machine."""

from __future__ import annotations

import pytest

from broker import db, policy
from broker.dispatch import SyntheticDispatcher
from broker.lifecycle import handle_action_request, get_request_model
from broker.models import Caller, RequestStatus


@pytest.mark.asyncio
async def test_allowed_request_completes(tmp_db, test_config, sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    caller_row = db.create_caller(tmp_db, "agent.test", "home-default")
    caller = Caller(**caller_row)
    dispatcher = SyntheticDispatcher()

    req, result = await handle_action_request(
        caller=caller,
        tool="hello-rest",
        op="greet",
        arguments={"name": "test"},
        reason="test",
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    assert req.status == RequestStatus.COMPLETED
    assert result is not None
    assert result["synthetic"] is True


@pytest.mark.asyncio
async def test_review_request_pending(tmp_db, test_config, sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    caller_row = db.create_caller(tmp_db, "agent.test", "tasks-agent")
    caller = Caller(**caller_row)
    dispatcher = SyntheticDispatcher()

    req, result = await handle_action_request(
        caller=caller,
        tool="tasks",
        op="delete_object",
        arguments={"type": "task", "id": "t1"},
        reason="delete please",
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    assert req.status == RequestStatus.PENDING_REVIEW
    assert result is None
    assert req.expires_at is not None


@pytest.mark.asyncio
async def test_denied_request(tmp_db, test_config, sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    caller_row = db.create_caller(tmp_db, "agent.test", "home-default")
    caller = Caller(**caller_row)
    dispatcher = SyntheticDispatcher()

    req, result = await handle_action_request(
        caller=caller,
        tool="admin",
        op="do_stuff",
        arguments={},
        reason=None,
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    assert req.status == RequestStatus.DENIED
    assert result is None


@pytest.mark.asyncio
async def test_failed_dispatch(tmp_db, test_config, sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    caller_row = db.create_caller(tmp_db, "agent.test", "home-default")
    caller = Caller(**caller_row)
    dispatcher = SyntheticDispatcher()

    req, result = await handle_action_request(
        caller=caller,
        tool="hello-rest",
        op="greet",
        arguments={"__synthetic_outcome": "fail"},
        reason="test failure",
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    assert req.status == RequestStatus.FAILED
    assert result is None


@pytest.mark.asyncio
async def test_arguments_redacted_in_storage(tmp_db, test_config, sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    caller_row = db.create_caller(tmp_db, "agent.test", "home-default")
    caller = Caller(**caller_row)
    dispatcher = SyntheticDispatcher()

    req, _ = await handle_action_request(
        caller=caller,
        tool="hello-rest",
        op="greet",
        arguments={"password": "s3cret", "query": "hello"},
        reason=None,
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    # Check the stored args are redacted
    import json
    raw_row = db.get_request(tmp_db, req.id)
    stored_args = json.loads(raw_row["args_json"])
    assert stored_args["password"] == "[REDACTED]"
    assert stored_args["query"] == "hello"


@pytest.mark.asyncio
async def test_get_request_model(tmp_db, test_config, sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    caller_row = db.create_caller(tmp_db, "agent.test", "home-default")
    caller = Caller(**caller_row)
    dispatcher = SyntheticDispatcher()

    req, _ = await handle_action_request(
        caller=caller,
        tool="hello-rest",
        op="greet",
        arguments={},
        reason=None,
        conn=tmp_db,
        dispatcher=dispatcher,
        config=test_config,
    )

    fetched = get_request_model(tmp_db, req.id)
    assert fetched is not None
    assert fetched.caller == "agent.test"
    assert fetched.profile == "home-default"
