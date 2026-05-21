"""Message store protocol and implementations.

Tracks the request_id ↔ message_id mapping so the bot knows which
Discord messages correspond to which broker requests. Persistent state
is broker-owned; the in-memory implementation is only for tests.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from discord_approver.broker_client import BrokerClient
from discord_approver.models import StoredMessage


@runtime_checkable
class MessageStore(Protocol):
    """Protocol for the request_id ↔ message_id mapping store."""

    async def upsert(self, request_id: int, message_id: int, status: str) -> None:
        """Insert or update a message mapping."""
        ...

    async def get(self, request_id: int) -> StoredMessage | None:
        """Get a stored message by request ID."""
        ...

    async def list_all(self) -> list[StoredMessage]:
        """List all stored message mappings."""
        ...

    async def delete(self, request_id: int) -> None:
        """Delete a message mapping."""
        ...


class BrokerMessageStore:
    """Broker-backed message store for persistent approval UI state."""

    def __init__(self, broker: BrokerClient) -> None:
        self._broker = broker

    async def upsert(self, request_id: int, message_id: int, status: str) -> None:
        await self._broker.upsert_approval_message(request_id, message_id, status)

    async def get(self, request_id: int) -> StoredMessage | None:
        return await self._broker.get_approval_message(request_id)

    async def list_all(self) -> list[StoredMessage]:
        return await self._broker.list_approval_messages()

    async def delete(self, request_id: int) -> None:
        await self._broker.delete_approval_message(request_id)


class InMemoryMessageStore:
    """In-memory message store for testing."""

    def __init__(self) -> None:
        self._store: dict[int, StoredMessage] = {}

    async def upsert(self, request_id: int, message_id: int, status: str) -> None:
        now = int(time.time())
        existing = self._store.get(request_id)
        self._store[request_id] = StoredMessage(
            request_id=request_id,
            message_id=message_id,
            last_status=status,
            posted_at=existing.posted_at if existing else now,
            updated_at=now,
        )

    async def get(self, request_id: int) -> StoredMessage | None:
        return self._store.get(request_id)

    async def list_all(self) -> list[StoredMessage]:
        return sorted(self._store.values(), key=lambda m: m.request_id)

    async def delete(self, request_id: int) -> None:
        self._store.pop(request_id, None)
