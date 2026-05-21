"""Shared test fixtures for the broker test suite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from broker import db, policy
from broker.config import Config


def sample_policy(policy_name: str) -> dict:
    """Return a named policy preset for tests."""
    read_ops = {
        "find_tasks": "allow",
        "find_tasks_by_date": "allow",
        "get_task": "allow",
        "find_projects": "allow",
        "find_sections": "allow",
        "find_comments": "allow",
        "find_labels": "allow",
        "find_reminders": "allow",
        "get_overview": "allow",
        "user_info": "allow",
    }
    task_write_ops = {
        "add_tasks": "allow",
        "update_tasks": "allow",
        "complete_tasks": "allow",
        "uncomplete_tasks": "allow",
        "reschedule_tasks": "allow",
        "add_project": "allow",
        "update_project": "allow",
        "archive_project": "allow",
        "unarchive_project": "allow",
        "add_section": "allow",
        "update_section": "allow",
        "add_comment": "allow",
        "update_comment": "allow",
        "add_label": "allow",
        "add_reminder": "allow",
        "update_reminder": "allow",
    }
    music_ops = {
        "get_status": "allow",
        "search_media": "allow",
        "search_music": "allow",
        "get_user_playlists": "allow",
        "get_playlist_tracks": "allow",
        "get_available_devices": "allow",
        "play_item": "allow",
        "play_playlist": "allow",
        "play_album": "allow",
        "pause_playback": "allow",
        "resume_playback": "allow",
        "next_track": "allow",
        "previous_track": "allow",
        "set_volume": "allow",
        "toggle_shuffle": "allow",
        "set_repeat_mode": "allow",
        "transfer_playback": "allow",
    }

    if policy_name in {"home-default", "readonly"}:
        return policy.normalize_policy({
            "tools": {
                "hello-rest": {"operations": {"greet": "allow"}},
                "time-mcp": {
                    "operations": {"current_time": "allow", "time_in": "allow", "read_state": "allow", "today": "allow"}
                },
            },
            "auto_grant_ttl_seconds": 3600 if policy_name == "home-default" else None,
        })
    if policy_name == "approver":
        return policy.normalize_policy({
            "broker_ops": [
                "broker.approve",
                "broker.reject",
                "broker.list_requests",
                "broker.audit",
                "broker.approval_messages.read",
                "broker.approval_messages.write",
            ]
        })
    if policy_name == "registry-admin":
        return policy.normalize_policy({"broker_ops": ["broker.registry.reload"]})
    if policy_name == "control-panel-admin":
        return policy.normalize_policy({
            "broker_ops": ["broker.admin.*", "broker.list_requests", "broker.audit"]
        })
    if policy_name in {"tasks-agent", "hermes-minerva"}:
        return policy.normalize_policy({
            "tools": {
                "tasks": {"operations": {**read_ops, **task_write_ops, "delete_object": "review"}},
                "task-api": {"operations": {**read_ops, **task_write_ops, "delete_object": "review"}},
                "calendar": {
                    "operations": {
                        "today": "allow",
                        "upcoming": "allow",
                        "search": "allow",
                        "event_details": "allow",
                    }
                },
            },
            "auto_grant_ttl_seconds": 3600,
        })
    if policy_name == "tasks-readonly":
        return policy.normalize_policy({"tools": {"tasks": {"operations": read_ops}}})
    if policy_name in {"media-agent", "hermes-kira"}:
        return policy.normalize_policy({"tools": {"media": {"operations": music_ops}, "music": {"operations": music_ops}}})
    if policy_name == "mcp-tester":
        return policy.normalize_policy({
            "tools": {
                "time-mcp": {
                    "operations": {
                        "current_time": "allow",
                        "skip_dance": "review",
                    }
                }
            },
            "auto_grant_ttl_seconds": 0,
        })
    return policy.empty_policy()


def create_test_caller(conn: sqlite3.Connection, name: str, policy_name: str | None = None) -> dict:
    row = db.create_caller(conn, name)
    if policy_name is not None:
        policy.upsert_policy(conn, row["id"], sample_policy(policy_name))
    return row


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a fresh SQLite database in a temp directory."""
    db_path = tmp_path / "test.sqlite3"
    conn = db.init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def test_config(tmp_path: Path) -> Config:
    """Create a test Config pointing at temp directories."""
    return Config(
        bind_addr="127.0.0.1:0",
        state_dir=tmp_path / "state",
        tools_dir=tmp_path / "tools",
        approval_timeout_seconds=86400,
        grant_default_ttl_seconds=3600,
        allow_unknown_tools=True,
    )
