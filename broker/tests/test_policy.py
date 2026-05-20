"""Tests for policy.py - YAML loading + decide() branches."""

from __future__ import annotations

from broker import policy
from broker.models import Grant, PolicyInput


def test_load_profiles(sample_profiles_dir):
    profiles = policy.load_profiles(sample_profiles_dir)
    assert "home-default" in profiles
    assert "approver" in profiles
    assert "readonly" in profiles
    assert "registry-admin" in profiles
    assert "media-agent" in profiles
    assert "tasks-agent" in profiles
    assert "tasks-readonly" in profiles


def test_media_agent_allows_read_op(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    inp = PolicyInput(
        caller_id=1, profile="media-agent",
        tool="media", op="get_current_playback",
    )
    dec = policy.decide(inp)
    assert dec.effect == "allow"
    assert dec.risk == "read"


def test_media_agent_allows_write_op_without_review(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    inp = PolicyInput(
        caller_id=1, profile="media-agent",
        tool="media", op="set_volume",
    )
    dec = policy.decide(inp)
    assert dec.effect == "allow"
    assert dec.risk == "write"


def test_tasks_agent_allows_read_ops(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    for op in ("find_tasks", "find_projects", "get_overview", "user_info"):
        dec = policy.decide(PolicyInput(caller_id=1, profile="tasks-agent", tool="tasks", op=op))
        assert dec.effect == "allow"


def test_tasks_agent_allows_write_ops_without_review(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    for op in ("add_tasks", "update_tasks", "complete_tasks", "reschedule_tasks", "add_comment"):
        dec = policy.decide(PolicyInput(caller_id=1, profile="tasks-agent", tool="tasks", op=op))
        assert dec.effect == "allow"


def test_tasks_agent_sends_delete_to_review(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    dec = policy.decide(PolicyInput(caller_id=1, profile="tasks-agent", tool="tasks", op="delete_object"))
    assert dec.effect == "review"
    assert dec.risk == "destructive"


def test_tasks_readonly_allows_only_reads(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    for op in ("find_tasks", "find_projects", "get_task", "user_info"):
        dec = policy.decide(PolicyInput(caller_id=1, profile="tasks-readonly", tool="tasks", op=op))
        assert dec.effect == "allow"

    for op in ("add_tasks", "update_tasks", "complete_tasks", "delete_object"):
        dec = policy.decide(PolicyInput(caller_id=1, profile="tasks-readonly", tool="tasks", op=op))
        assert dec.effect == "deny"


def test_decide_denied_tool(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    inp = PolicyInput(
        caller_id=1, profile="home-default",
        tool="admin", op="do_stuff",
    )
    dec = policy.decide(inp)
    assert dec.effect == "deny"


def test_media_agent_denies_delete_style_ops(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    inp = PolicyInput(
        caller_id=1, profile="media-agent",
        tool="media", op="delete_playlist",
    )
    dec = policy.decide(inp)
    assert dec.effect == "deny"


def test_decide_unknown_profile(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    inp = PolicyInput(
        caller_id=1, profile="nonexistent",
        tool="media", op="get_state",
    )
    dec = policy.decide(inp)
    assert dec.effect == "deny"
    assert "not found" in dec.reason


def test_home_default_does_not_grant_media_or_tasks(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    for tool, op in (("media", "set_volume"), ("tasks", "find_tasks"), ("tasks", "add_tasks")):
        dec = policy.decide(PolicyInput(caller_id=1, profile="home-default", tool=tool, op=op))
        assert dec.effect == "deny"


def test_decide_risk_class_default_read(sample_profiles_dir):
    """An allowed tool with a read op that doesn't match any specific rule."""
    policy.load_profiles(sample_profiles_dir)
    inp = PolicyInput(
        caller_id=1, profile="home-default",
        tool="time-mcp", op="read_state",
    )
    dec = policy.decide(inp)
    assert dec.effect == "allow"
    assert dec.risk == "read"


def test_decide_with_active_grant(sample_profiles_dir):
    """An active grant skips rule evaluation and allows."""
    import time
    policy.load_profiles(sample_profiles_dir)
    grant = Grant(
        id=1, caller_id=1, tool="media", op="skip_track",
        expires_at=int(time.time()) + 3600,
    )
    inp = PolicyInput(
        caller_id=1, profile="home-default",
        tool="media", op="skip_track",
        active_grants=[grant],
    )
    dec = policy.decide(inp)
    assert dec.effect == "allow"
    assert "grant" in dec.reason.lower()


def test_decide_fail_closed_no_match(sample_profiles_dir):
    """Tool not in allowed_tools and no op rules -> deny (fail closed)."""
    policy.load_profiles(sample_profiles_dir)
    inp = PolicyInput(
        caller_id=1, profile="home-default",
        tool="unknown_tool", op="do_something",
    )
    dec = policy.decide(inp)
    assert dec.effect == "deny"


def test_readonly_profile_does_not_grant_media_or_tasks(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    for tool, op in (("media", "get_current_playback"), ("media", "set_volume"), ("tasks", "get_task"), ("tasks", "find_tasks")):
        dec = policy.decide(PolicyInput(caller_id=1, profile="readonly", tool=tool, op=op))
        assert dec.effect == "deny"


def test_example_tools_are_allowed_for_home_and_readonly(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    cases = [
        ("home-default", "hello-rest", "greet"),
        ("home-default", "time-mcp", "current_time"),
        ("home-default", "time-mcp", "time_in"),
        ("readonly", "hello-rest", "greet"),
        ("readonly", "time-mcp", "current_time"),
        ("readonly", "time-mcp", "time_in"),
    ]
    for profile, tool, op in cases:
        dec = policy.decide(PolicyInput(caller_id=1, profile=profile, tool=tool, op=op))
        assert dec.effect == "allow"


def test_registry_admin_only_allows_registry_reload(sample_profiles_dir):
    policy.load_profiles(sample_profiles_dir)
    dec = policy.decide(PolicyInput(
        caller_id=1, profile="registry-admin", tool="broker", op="registry.reload",
    ))
    assert dec.effect == "allow"
    dec = policy.decide(PolicyInput(
        caller_id=1, profile="registry-admin", tool="broker", op="approve",
    ))
    assert dec.effect == "deny"
