"""Reconciler — the polling loop that syncs broker state to Discord messages.

The reconciler is the core logic of the bot. It polls the broker for new
pending requests, posts approval cards, and edits existing messages when
request state transitions. It uses the ApprovalUI protocol so the Discord
surface is swappable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

from discord_approver.broker_client import BrokerClient
from discord_approver.models import Request, RequestStatus
from discord_approver.state import MessageStore

logger = logging.getLogger(__name__)

# Terminal statuses — once a request is in one of these, we stop tracking it
_TERMINAL_STATUSES = frozenset({
    RequestStatus.APPROVED,
    RequestStatus.REJECTED,
    RequestStatus.EXPIRED,
    RequestStatus.DENIED,
    RequestStatus.COMPLETED,
    RequestStatus.FAILED,
})


_TERMINAL_STATUS_VALUES = frozenset(s.value for s in _TERMINAL_STATUSES)


@runtime_checkable
class ApprovalUI(Protocol):
    """Protocol for the approval UI surface (Discord, ntfy, etc.)."""

    async def post_card(self, request: Request) -> int:
        """Post an approval card. Returns the message_id."""
        ...

    async def edit_card(self, message_id: int, request: Request | None) -> None:
        """Edit an existing card. If request is None, the request no longer exists."""
        ...

    async def delete_card(self, message_id: int) -> None:
        """Delete a message from the channel."""
        ...


class Reconciler:
    """Polls the broker and reconciles state with the approval UI.

    On each tick:
    1. Fetch new pending requests we haven't posted yet → post cards
    2. Re-check tracked requests for state transitions → edit cards
    """

    def __init__(
        self,
        broker: BrokerClient,
        store: MessageStore,
        ui: ApprovalUI,
        poll_interval: float = 10.0,
        max_terminal_messages: int = 25,
    ) -> None:
        self._broker = broker
        self._store = store
        self._ui = ui
        self._poll_interval = poll_interval
        self._max_terminal = max_terminal_messages
        self._last_seen_id: int | None = None

    async def run_forever(self) -> None:
        """Main loop. Runs startup_sync once, then ticks forever."""
        await self.startup_sync()
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                logger.info("reconciler cancelled, shutting down")
                raise
            except Exception:
                logger.exception("reconciler tick failed")
            await asyncio.sleep(self._poll_interval)

    async def startup_sync(self) -> None:
        """On startup: sync all tracked messages with broker state, post
        cards for any pending requests we don't have messages for."""
        logger.info("startup sync: reconciling existing state")

        # 1. Re-check all tracked messages
        stored = self._store.list_all()
        for msg in stored:
            try:
                current = await self._broker.get_request(msg.request_id)
                if current is None:
                    # Request no longer exists in broker
                    await self._ui.edit_card(msg.message_id, None)
                    self._store.delete(msg.request_id)
                elif current.status.value != msg.last_status:
                    await self._ui.edit_card(msg.message_id, current)
                    self._store.upsert(msg.request_id, msg.message_id, current.status.value)
                    if current.status in _TERMINAL_STATUSES:
                        # Keep in store for audit trail, but update status
                        pass
            except Exception:
                logger.exception("startup sync failed for request %d", msg.request_id)

        # 2. Fetch all pending requests and post cards for ones we don't have
        try:
            pending = await self._broker.list_pending()
            for req in pending:
                existing = self._store.get(req.id)
                if existing is None:
                    logger.info("posting card for request %d (%s.%s)", req.id, req.tool, req.op)
                    message_id = await self._ui.post_card(req)
                    self._store.upsert(req.id, message_id, req.status.value)
                # Track the highest ID we've seen
                if self._last_seen_id is None or req.id > self._last_seen_id:
                    self._last_seen_id = req.id
        except Exception:
            logger.exception("startup sync: failed to fetch pending requests")

        await self._cleanup_terminal()
        logger.info("startup sync complete")

    async def tick(self) -> None:
        """One polling cycle."""
        # 1. Fetch new pending requests
        await self._fetch_and_post_new()

        # 2. Re-check tracked active requests for state transitions
        await self._refresh_tracked()

        # 3. Prune old terminal messages to keep the channel clean
        await self._cleanup_terminal()

    async def _fetch_and_post_new(self) -> None:
        """Fetch pending requests we haven't seen yet and post cards."""
        try:
            new_pending = await self._broker.list_pending(after_id=self._last_seen_id)
        except Exception:
            logger.exception("failed to fetch pending requests from broker")
            return

        for req in new_pending:
            existing = self._store.get(req.id)
            if existing is not None:
                continue  # Already tracked

            try:
                logger.info(
                    "posting card for request %d (%s.%s)",
                    req.id, req.tool, req.op,
                )
                message_id = await self._ui.post_card(req)
                self._store.upsert(req.id, message_id, req.status.value)
            except Exception:
                logger.exception("failed to post card for request %d", req.id)

            if self._last_seen_id is None or req.id > self._last_seen_id:
                self._last_seen_id = req.id

    async def _refresh_tracked(self) -> None:
        """Re-check all tracked non-terminal requests for state changes."""
        stored = self._store.list_all()
        for msg in stored:
            # Skip already-terminal messages to avoid unnecessary broker calls
            if msg.last_status in {s.value for s in _TERMINAL_STATUSES}:
                continue

            try:
                current = await self._broker.get_request(msg.request_id)
            except Exception:
                logger.exception(
                    "failed to fetch request %d from broker", msg.request_id
                )
                continue

            if current is None:
                # Request disappeared
                try:
                    await self._ui.edit_card(msg.message_id, None)
                except Exception:
                    logger.exception(
                        "failed to edit card for disappeared request %d",
                        msg.request_id,
                    )
                self._store.delete(msg.request_id)
                continue

            if current.status.value != msg.last_status:
                logger.info(
                    "request %d transitioned %s → %s",
                    msg.request_id, msg.last_status, current.status.value,
                )
                try:
                    await self._ui.edit_card(msg.message_id, current)
                except Exception:
                    logger.exception(
                        "failed to edit card for request %d", msg.request_id
                    )
                self._store.upsert(
                    msg.request_id, msg.message_id, current.status.value
                )

    async def _cleanup_terminal(self) -> None:
        """Delete old terminal messages beyond the configured cap.

        Keeps at most max_terminal_messages terminal (closed) messages.
        Pending messages are never pruned. 0 = keep all (no cleanup).
        """
        if self._max_terminal <= 0:
            return

        stored = self._store.list_all()
        terminal = [
            m for m in stored if m.last_status in _TERMINAL_STATUS_VALUES
        ]

        if len(terminal) <= self._max_terminal:
            return

        # Sort by posted_at ascending (oldest first), prune the excess
        terminal.sort(key=lambda m: m.posted_at)
        to_prune = terminal[: len(terminal) - self._max_terminal]

        for msg in to_prune:
            try:
                await self._ui.delete_card(msg.message_id)
                self._store.delete(msg.request_id)
                logger.info(
                    "pruned terminal message for request %d (status=%s)",
                    msg.request_id, msg.last_status,
                )
            except Exception:
                logger.exception(
                    "failed to prune message for request %d", msg.request_id
                )
