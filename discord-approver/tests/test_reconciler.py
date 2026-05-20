"""Tests for the reconciler — core bot logic with all mocks.

These tests verify the reconciler's behavior without Discord or a real broker.
"""

from __future__ import annotations

import pytest

from discord_approver.broker_client import MockBrokerClient
from discord_approver.models import RequestStatus
from discord_approver.reconciler import Reconciler
from discord_approver.state import InMemoryMessageStore


class MockApprovalUI:
    """Mock ApprovalUI that records calls for assertion."""

    def __init__(self):
        self.posted: list[tuple[int, int]] = []  # (request_id, message_id)
        self.edited: list[tuple[int, str | None]] = []  # (message_id, status)
        self.deleted: list[int] = []  # message_ids
        self._next_message_id = 1000

    async def post_card(self, request) -> int:
        msg_id = self._next_message_id
        self._next_message_id += 1
        self.posted.append((request.id, msg_id))
        return msg_id

    async def edit_card(self, message_id: int, request) -> None:
        status = request.status.value if request else None
        self.edited.append((message_id, status))

    async def delete_card(self, message_id: int) -> None:
        self.deleted.append(message_id)


@pytest.fixture
def broker():
    return MockBrokerClient()


@pytest.fixture
def store():
    return InMemoryMessageStore()


@pytest.fixture
def ui():
    return MockApprovalUI()


@pytest.fixture
def reconciler(broker, store, ui):
    return Reconciler(broker=broker, store=store, ui=ui, poll_interval=0.1)


class TestStartupSync:
    async def test_posts_cards_for_pending_requests(self, reconciler, broker, ui):
        broker.inject(caller="agent.hermes", profile="home", tool="media", op="play")
        broker.inject(caller="agent.hermes", profile="home", tool="media", op="skip")
        await reconciler.startup_sync()
        assert len(ui.posted) == 2

    async def test_no_posts_when_no_pending(self, reconciler, ui):
        await reconciler.startup_sync()
        assert len(ui.posted) == 0

    async def test_edits_stale_messages(self, reconciler, broker, store, ui):
        # Simulate: bot had tracked a request, but it was approved externally
        req = broker.inject(caller="a", profile="p", tool="t", op="o")
        store.upsert(req.id, 5000, "pending_review")
        # Now approve it in the broker
        await broker.approve(req.id, "admin")
        await reconciler.startup_sync()
        # Should have edited the message
        assert len(ui.edited) == 1
        assert ui.edited[0] == (5000, "approved")

    async def test_does_not_repost_already_tracked(self, reconciler, broker, store, ui):
        req = broker.inject(caller="a", profile="p", tool="t", op="o")
        store.upsert(req.id, 5000, "pending_review")
        await reconciler.startup_sync()
        # Should NOT post a new card (already tracked)
        assert len(ui.posted) == 0


class TestTick:
    async def test_posts_new_pending(self, reconciler, broker, ui):
        await reconciler.startup_sync()
        broker.inject(caller="a", profile="p", tool="t", op="o")
        await reconciler.tick()
        assert len(ui.posted) == 1

    async def test_does_not_repost(self, reconciler, broker, ui):
        broker.inject(caller="a", profile="p", tool="t", op="o")
        await reconciler.startup_sync()
        await reconciler.tick()
        # Only one post total (from startup)
        assert len(ui.posted) == 1

    async def test_edits_on_state_transition(self, reconciler, broker, store, ui):
        req = broker.inject(caller="a", profile="p", tool="t", op="o")
        await reconciler.startup_sync()
        assert len(ui.posted) == 1
        msg_id = ui.posted[0][1]

        # Approve the request externally
        await broker.approve(req.id, "admin")
        await reconciler.tick()

        # Should have edited the message
        assert len(ui.edited) == 1
        assert ui.edited[0] == (msg_id, "approved")

    async def test_handles_expired(self, reconciler, broker, store, ui):
        req = broker.inject(caller="a", profile="p", tool="t", op="o")
        await reconciler.startup_sync()
        msg_id = ui.posted[0][1]

        # Expire it
        broker._requests[req.id] = req.model_copy(
            update={"status": RequestStatus.EXPIRED}
        )
        await reconciler.tick()

        assert len(ui.edited) == 1
        assert ui.edited[0] == (msg_id, "expired")

    async def test_skips_already_terminal(self, reconciler, broker, store, ui):
        req = broker.inject(caller="a", profile="p", tool="t", op="o")
        await reconciler.startup_sync()

        # Approve and let reconciler detect it
        await broker.approve(req.id, "admin")
        await reconciler.tick()
        assert len(ui.edited) == 1

        # On next tick, should NOT re-check (it's terminal)
        ui.edited.clear()
        await reconciler.tick()
        assert len(ui.edited) == 0

    async def test_handles_disappeared_request(self, reconciler, broker, store, ui):
        req = broker.inject(caller="a", profile="p", tool="t", op="o")
        await reconciler.startup_sync()
        msg_id = ui.posted[0][1]

        # Remove the request from broker
        del broker._requests[req.id]
        await reconciler.tick()

        assert len(ui.edited) == 1
        assert ui.edited[0] == (msg_id, None)


class TestMultipleRequests:
    async def test_multiple_new_requests(self, reconciler, broker, ui):
        for i in range(5):
            broker.inject(caller="a", profile="p", tool="t", op=f"op_{i}")
        await reconciler.startup_sync()
        assert len(ui.posted) == 5

    async def test_mixed_state_transitions(self, reconciler, broker, ui, store):
        r1 = broker.inject(caller="a", profile="p", tool="t", op="read")
        r2 = broker.inject(caller="a", profile="p", tool="t", op="write")
        r3 = broker.inject(caller="a", profile="p", tool="t", op="delete")
        await reconciler.startup_sync()
        assert len(ui.posted) == 3

        # Different transitions
        await broker.approve(r1.id, "admin")
        await broker.reject(r2.id, "admin", "nope")
        # r3 stays pending

        await reconciler.tick()
        assert len(ui.edited) == 2  # r1 and r2 edited, r3 unchanged


class TestCleanup:
    async def test_prunes_oldest_terminal_beyond_cap(self, broker, store, ui):
        reconciler = Reconciler(
            broker=broker, store=store, ui=ui,
            poll_interval=0.1, max_terminal_messages=3,
        )
        # Create 5 requests, approve them all
        reqs = []
        for i in range(5):
            r = broker.inject(caller="a", profile="p", tool="t", op=f"op_{i}")
            reqs.append(r)
        await reconciler.startup_sync()
        assert len(ui.posted) == 5

        # Approve all 5
        for r in reqs:
            await broker.approve(r.id, "admin")
        await reconciler.tick()

        # 5 terminal, cap=3 → 2 oldest should be deleted
        assert len(ui.deleted) == 2
        # The deleted ones should be the first 2 posted
        assert ui.deleted[0] == ui.posted[0][1]
        assert ui.deleted[1] == ui.posted[1][1]
        # Store should have 3 remaining
        assert len(store.list_all()) == 3

    async def test_does_not_prune_pending(self, broker, store, ui):
        reconciler = Reconciler(
            broker=broker, store=store, ui=ui,
            poll_interval=0.1, max_terminal_messages=2,
        )
        # Create 5 pending requests
        for i in range(5):
            broker.inject(caller="a", profile="p", tool="t", op=f"op_{i}")
        await reconciler.startup_sync()
        await reconciler.tick()
        # All pending → nothing pruned
        assert len(ui.deleted) == 0
        assert len(store.list_all()) == 5

    async def test_no_prune_when_disabled(self, broker, store, ui):
        reconciler = Reconciler(
            broker=broker, store=store, ui=ui,
            poll_interval=0.1, max_terminal_messages=0,  # disabled
        )
        reqs = []
        for i in range(5):
            r = broker.inject(caller="a", profile="p", tool="t", op=f"op_{i}")
            reqs.append(r)
        await reconciler.startup_sync()
        for r in reqs:
            await broker.approve(r.id, "admin")
        await reconciler.tick()
        # max=0 means disabled → nothing pruned
        assert len(ui.deleted) == 0

    async def test_no_prune_when_under_cap(self, broker, store, ui):
        reconciler = Reconciler(
            broker=broker, store=store, ui=ui,
            poll_interval=0.1, max_terminal_messages=10,
        )
        reqs = []
        for i in range(3):
            r = broker.inject(caller="a", profile="p", tool="t", op=f"op_{i}")
            reqs.append(r)
        await reconciler.startup_sync()
        for r in reqs:
            await broker.approve(r.id, "admin")
        await reconciler.tick()
        # 3 terminal < 10 cap → nothing pruned
        assert len(ui.deleted) == 0
