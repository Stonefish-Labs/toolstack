"""Shared test fixtures for the broker test suite."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from broker import db
from broker.config import Config


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a fresh SQLite database in a temp directory."""
    db_path = tmp_path / "test.sqlite3"
    conn = db.init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def sample_profiles_dir(tmp_path: Path) -> Path:
    """Create a temp directory with sample policy profiles."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()

    (profiles_dir / "home-default.yaml").write_text(
        """
profile: home-default
allowed_tools:
  - hello-rest
  - time-mcp
denied_tools:
  - admin
  - tasks
allowed_ops:
  - "hello-rest.greet"
  - "time-mcp.current_time"
  - "time-mcp.time_in"
denied_ops:
  - "*.delete_*"
risk_class_default:
  read: allow
  write: review
  destructive: deny
auto_grant_ttl_seconds: 3600
"""
    )

    (profiles_dir / "approver.yaml").write_text(
        """
profile: approver
allowed_ops:
  - "broker.approve"
  - "broker.reject"
  - "broker.list_requests"
  - "broker.audit"
"""
    )

    (profiles_dir / "readonly.yaml").write_text(
        """
profile: readonly
allowed_tools:
  - hello-rest
  - time-mcp
denied_tools:
  - media
  - tasks
allowed_ops:
  - "*.get_*"
  - "*.list_*"
  - "hello-rest.greet"
  - "time-mcp.current_time"
  - "time-mcp.time_in"
risk_class_default:
  read: allow
  write: deny
  destructive: deny
"""
    )

    (profiles_dir / "media-agent.yaml").write_text(
        """
profile: media-agent
allowed_tools:
  - media
allowed_ops:
  - "media.get_current_playback"
  - "media.search_media"
  - "media.get_user_playlists"
  - "media.get_playlist_tracks"
  - "media.get_available_devices"
  - "media.play_track"
  - "media.play_playlist"
  - "media.play_album"
  - "media.pause_playback"
  - "media.resume_playback"
  - "media.next_track"
  - "media.previous_track"
  - "media.set_volume"
  - "media.toggle_shuffle"
  - "media.set_repeat_mode"
  - "media.transfer_playback"
denied_ops:
  - "media.clear_*"
  - "media.reset_*"
  - "media.delete_*"
  - "*.delete_*"
risk_class_default:
  read: allow
  write: allow
  destructive: deny
"""
    )

    (profiles_dir / "tasks-agent.yaml").write_text(
        """
profile: tasks-agent
allowed_tools:
  - tasks
allowed_ops:
  - "tasks.find_tasks"
  - "tasks.find_tasks_by_date"
  - "tasks.get_task"
  - "tasks.find_projects"
  - "tasks.find_sections"
  - "tasks.find_comments"
  - "tasks.find_labels"
  - "tasks.find_reminders"
  - "tasks.get_overview"
  - "tasks.user_info"
  - "tasks.add_tasks"
  - "tasks.update_tasks"
  - "tasks.complete_tasks"
  - "tasks.uncomplete_tasks"
  - "tasks.reschedule_tasks"
  - "tasks.add_project"
  - "tasks.update_project"
  - "tasks.archive_project"
  - "tasks.unarchive_project"
  - "tasks.add_section"
  - "tasks.update_section"
  - "tasks.add_comment"
  - "tasks.update_comment"
  - "tasks.add_label"
  - "tasks.add_reminder"
  - "tasks.update_reminder"
review_ops:
  - "tasks.delete_object"
denied_ops:
  - "tasks.clear_*"
  - "tasks.reset_*"
  - "tasks.destroy_*"
risk_class_default:
  read: allow
  write: allow
  destructive: review
auto_grant_ttl_seconds: 3600
"""
    )

    (profiles_dir / "tasks-readonly.yaml").write_text(
        """
profile: tasks-readonly
allowed_tools:
  - tasks
allowed_ops:
  - "tasks.find_tasks"
  - "tasks.find_tasks_by_date"
  - "tasks.get_task"
  - "tasks.find_projects"
  - "tasks.find_sections"
  - "tasks.find_comments"
  - "tasks.find_labels"
  - "tasks.find_reminders"
  - "tasks.get_overview"
  - "tasks.user_info"
denied_ops:
  - "tasks.add_*"
  - "tasks.update_*"
  - "tasks.complete_*"
  - "tasks.uncomplete_*"
  - "tasks.reschedule_*"
  - "tasks.archive_*"
  - "tasks.unarchive_*"
  - "tasks.delete_object"
risk_class_default:
  read: allow
  write: deny
  destructive: deny
"""
    )

    (profiles_dir / "registry-admin.yaml").write_text(
        """
profile: registry-admin
allowed_ops:
  - "broker.registry.reload"
"""
    )

    return profiles_dir


@pytest.fixture
def test_config(tmp_path: Path, sample_profiles_dir: Path) -> Config:
    """Create a test Config pointing at temp directories."""
    return Config(
        bind_addr="127.0.0.1:0",
        state_dir=tmp_path / "state",
        tools_dir=tmp_path / "tools",
        policies_dir=sample_profiles_dir,
        approval_timeout_seconds=86400,
        grant_default_ttl_seconds=3600,
        allow_unknown_tools=True,
    )
