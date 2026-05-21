"""Tests for the MessageStore implementations."""

from __future__ import annotations

import pytest

from discord_approver.broker_client import MockBrokerClient
from discord_approver.state import BrokerMessageStore, InMemoryMessageStore


@pytest.fixture
def memory_store() -> InMemoryMessageStore:
    return InMemoryMessageStore()


@pytest.fixture
def broker_store() -> BrokerMessageStore:
    return BrokerMessageStore(MockBrokerClient())


class StoreAssertions:
    async def test_upsert_and_get(self, store):
        await store.upsert(1, 100, "pending_review")
        msg = await store.get(1)
        assert msg is not None
        assert msg.request_id == 1
        assert msg.message_id == 100
        assert msg.last_status == "pending_review"

    async def test_upsert_updates_existing(self, store):
        await store.upsert(1, 100, "pending_review")
        await store.upsert(1, 100, "approved")
        msg = await store.get(1)
        assert msg.last_status == "approved"

    async def test_get_nonexistent_returns_none(self, store):
        assert await store.get(999) is None

    async def test_list_all(self, store):
        await store.upsert(2, 200, "approved")
        await store.upsert(1, 100, "pending_review")
        all_msgs = await store.list_all()
        assert len(all_msgs) == 2
        assert [m.request_id for m in all_msgs] == [1, 2]

    async def test_delete(self, store):
        await store.upsert(1, 100, "pending_review")
        await store.delete(1)
        assert await store.get(1) is None

    async def test_delete_nonexistent_is_noop(self, store):
        await store.delete(999)  # Should not raise


class TestInMemoryMessageStore(StoreAssertions):
    @pytest.fixture
    def store(self, memory_store):
        return memory_store

    async def test_timestamps_set(self, store):
        await store.upsert(1, 100, "pending_review")
        msg = await store.get(1)
        assert msg.posted_at > 0
        assert msg.updated_at > 0


class TestBrokerMessageStore(StoreAssertions):
    @pytest.fixture
    def store(self, broker_store):
        return broker_store
