"""Tests for the MessageStore implementations."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from discord_approver.state import InMemoryMessageStore, SqliteMessageStore


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SqliteMessageStore:
    return SqliteMessageStore(tmp_path / "test.sqlite3")


@pytest.fixture
def memory_store() -> InMemoryMessageStore:
    return InMemoryMessageStore()


class TestSqliteMessageStore:
    def test_upsert_and_get(self, sqlite_store):
        sqlite_store.upsert(1, 100, "pending_review")
        msg = sqlite_store.get(1)
        assert msg is not None
        assert msg.request_id == 1
        assert msg.message_id == 100
        assert msg.last_status == "pending_review"

    def test_upsert_updates_existing(self, sqlite_store):
        sqlite_store.upsert(1, 100, "pending_review")
        sqlite_store.upsert(1, 100, "approved")
        msg = sqlite_store.get(1)
        assert msg.last_status == "approved"

    def test_get_nonexistent_returns_none(self, sqlite_store):
        assert sqlite_store.get(999) is None

    def test_list_all(self, sqlite_store):
        sqlite_store.upsert(1, 100, "pending_review")
        sqlite_store.upsert(2, 200, "approved")
        sqlite_store.upsert(3, 300, "rejected")
        all_msgs = sqlite_store.list_all()
        assert len(all_msgs) == 3
        assert [m.request_id for m in all_msgs] == [1, 2, 3]

    def test_list_all_empty(self, sqlite_store):
        assert sqlite_store.list_all() == []

    def test_delete(self, sqlite_store):
        sqlite_store.upsert(1, 100, "pending_review")
        sqlite_store.delete(1)
        assert sqlite_store.get(1) is None

    def test_delete_nonexistent_is_noop(self, sqlite_store):
        sqlite_store.delete(999)  # Should not raise

    def test_timestamps_set(self, sqlite_store):
        sqlite_store.upsert(1, 100, "pending_review")
        msg = sqlite_store.get(1)
        assert msg.posted_at > 0
        assert msg.updated_at > 0


class TestInMemoryMessageStore:
    def test_upsert_and_get(self, memory_store):
        memory_store.upsert(1, 100, "pending_review")
        msg = memory_store.get(1)
        assert msg is not None
        assert msg.request_id == 1
        assert msg.message_id == 100

    def test_upsert_updates_existing(self, memory_store):
        memory_store.upsert(1, 100, "pending_review")
        memory_store.upsert(1, 100, "approved")
        msg = memory_store.get(1)
        assert msg.last_status == "approved"

    def test_get_nonexistent_returns_none(self, memory_store):
        assert memory_store.get(999) is None

    def test_list_all(self, memory_store):
        memory_store.upsert(2, 200, "approved")
        memory_store.upsert(1, 100, "pending_review")
        all_msgs = memory_store.list_all()
        assert len(all_msgs) == 2
        # Should be sorted by request_id
        assert all_msgs[0].request_id == 1

    def test_delete(self, memory_store):
        memory_store.upsert(1, 100, "pending_review")
        memory_store.delete(1)
        assert memory_store.get(1) is None

    def test_delete_nonexistent_is_noop(self, memory_store):
        memory_store.delete(999)  # Should not raise
