"""Tests for caller-scoped policy decisions."""

from __future__ import annotations

import time

from broker import db, policy
from broker.models import Grant, PolicyInput
from tests.conftest import sample_policy


def _input(tool: str, op: str, *, declared_risk: str | None = None, grants=None) -> PolicyInput:
    return PolicyInput(
        caller_id=1,
        caller="agent.test",
        tool=tool,
        op=op,
        declared_risk=declared_risk,
        active_grants=grants or [],
    )


def test_empty_policy_denies_by_default():
    dec = policy.decide(_input("music", "play_item"), policy.empty_policy())
    assert dec.effect == "deny"


def test_media_policy_allows_read_and_write_ops():
    caller_policy = sample_policy("media-agent")
    assert policy.decide(_input("media", "get_status"), caller_policy).effect == "allow"
    dec = policy.decide(_input("media", "set_volume"), caller_policy)
    assert dec.effect == "allow"
    assert dec.risk == "write"


def test_tasks_policy_allows_writes_and_reviews_delete():
    caller_policy = sample_policy("tasks-agent")
    for op in ("find_tasks", "add_tasks", "update_tasks", "complete_tasks", "add_comment"):
        assert policy.decide(_input("tasks", op), caller_policy).effect == "allow"
    dec = policy.decide(_input("tasks", "delete_object"), caller_policy)
    assert dec.effect == "review"
    assert dec.risk == "destructive"


def test_tasks_readonly_allows_only_reads():
    caller_policy = sample_policy("tasks-readonly")
    for op in ("find_tasks", "find_projects", "get_task", "user_info"):
        assert policy.decide(_input("tasks", op), caller_policy).effect == "allow"
    for op in ("add_tasks", "update_tasks", "complete_tasks", "delete_object"):
        assert policy.decide(_input("tasks", op), caller_policy).effect == "deny"


def test_declared_risk_overrides_name_heuristic():
    dec = policy.decide(
        _input("time-mcp", "today", declared_risk="read"),
        sample_policy("home-default"),
    )
    assert dec.effect == "allow"
    assert dec.risk == "read"


def test_active_grant_allows_without_policy_match():
    grant = Grant(
        id=1,
        caller_id=1,
        tool="music",
        op="play_item",
        expires_at=int(time.time()) + 3600,
    )
    dec = policy.decide(_input("music", "play_item", grants=[grant]), policy.empty_policy())
    assert dec.effect == "allow"
    assert "grant" in dec.reason.lower()


def test_broker_op_matching():
    caller_policy = sample_policy("control-panel-admin")
    assert policy.caller_allows_broker_op(caller_policy, "admin.callers.read")
    assert policy.caller_allows_broker_op(caller_policy, "audit")
    assert not policy.caller_allows_broker_op(caller_policy, "registry.reload")


def test_policy_roundtrip_to_db(tmp_db):
    caller = db.create_caller(tmp_db, "agent.roundtrip")
    source = sample_policy("tasks-agent")
    policy.upsert_policy(tmp_db, caller["id"], source)

    loaded = policy.caller_policy(tmp_db, caller["id"])
    assert loaded["tools"]["tasks"]["operations"]["delete_object"] == "review"
    assert loaded["auto_grant_ttl_seconds"] == 3600


def test_seeded_hermes_shapes():
    kira = sample_policy("hermes-kira")
    minerva = sample_policy("hermes-minerva")

    assert policy.decide(_input("music", "play_item", declared_risk="write"), kira).effect == "allow"
    assert policy.decide(_input("task-api", "find_tasks", declared_risk="read"), kira).effect == "deny"
    assert policy.decide(_input("task-api", "delete_object", declared_risk="destructive"), minerva).effect == "review"
    assert policy.decide(_input("calendar", "today", declared_risk="read"), minerva).effect == "allow"
