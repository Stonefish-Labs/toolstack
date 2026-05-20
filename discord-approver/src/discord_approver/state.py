"""Message store protocol and implementations.

Tracks the request_id ↔ message_id mapping so the bot knows which
Discord messages correspond to which broker requests.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from discord_approver.models import StoredMessage


@runtime_checkable
class MessageStore(Protocol):
    """Protocol for the request_id ↔ message_id mapping store."""

    def upsert(self, request_id: int, message_id: int, status: str) -> None:
        """Insert or update a message mapping."""
        ...

    def get(self, request_id: int) -> StoredMessage | None:
        """Get a stored message by request ID."""
        ...

    def list_all(self) -> list[StoredMessage]:
        """List all stored message mappings."""
        ...

    def delete(self, request_id: int) -> None:
        """Delete a message mapping."""
        ...


class SqliteMessageStore:
    """SQLite-backed message store.

    Uses a single connection with check_same_thread=False.
    All operations are synchronous (appropriate for our scale).
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                request_id   INTEGER PRIMARY KEY,
                message_id   INTEGER NOT NULL,
                last_status  TEXT NOT NULL,
                posted_at    INTEGER NOT NULL,
                updated_at   INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def upsert(self, request_id: int, message_id: int, status: str) -> None:
        now = int(time.time())
        self._conn.execute(
            """
            INSERT INTO messages (request_id, message_id, last_status, posted_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                last_status = excluded.last_status,
                updated_at = excluded.updated_at
            """,
            (request_id, message_id, status, now, now),
        )
        self._conn.commit()

    def get(self, request_id: int) -> StoredMessage | None:
        row = self._conn.execute(
            "SELECT request_id, message_id, last_status, posted_at, updated_at "
            "FROM messages WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredMessage(
            request_id=row[0],
            message_id=row[1],
            last_status=row[2],
            posted_at=row[3],
            updated_at=row[4],
        )

    def list_all(self) -> list[StoredMessage]:
        rows = self._conn.execute(
            "SELECT request_id, message_id, last_status, posted_at, updated_at "
            "FROM messages ORDER BY request_id"
        ).fetchall()
        return [
            StoredMessage(
                request_id=r[0],
                message_id=r[1],
                last_status=r[2],
                posted_at=r[3],
                updated_at=r[4],
            )
            for r in rows
        ]

    def delete(self, request_id: int) -> None:
        self._conn.execute("DELETE FROM messages WHERE request_id = ?", (request_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class InMemoryMessageStore:
    """In-memory message store for testing."""

    def __init__(self) -> None:
        self._store: dict[int, StoredMessage] = {}

    def upsert(self, request_id: int, message_id: int, status: str) -> None:
        now = int(time.time())
        existing = self._store.get(request_id)
        self._store[request_id] = StoredMessage(
            request_id=request_id,
            message_id=message_id,
            last_status=status,
            posted_at=existing.posted_at if existing else now,
            updated_at=now,
        )

    def get(self, request_id: int) -> StoredMessage | None:
        return self._store.get(request_id)

    def list_all(self) -> list[StoredMessage]:
        return sorted(self._store.values(), key=lambda m: m.request_id)

    def delete(self, request_id: int) -> None:
        self._store.pop(request_id, None)
