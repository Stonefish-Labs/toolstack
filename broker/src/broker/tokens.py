"""Bearer token generation, hashing, and verification.

Tokens are secrets.token_urlsafe(32) → SHA-256 for storage.
Raw tokens are shown to the operator exactly once.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

from broker import db
from broker.models import Caller


def generate_raw_token() -> str:
    """Return a cryptographically-random URL-safe token (32 bytes → base64)."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token_for_caller(
    conn: sqlite3.Connection, caller_id: int
) -> tuple[str, str]:
    """Generate, hash, store. Return (raw_token, hash_prefix).

    The raw token is shown to the operator once and never stored.
    The hash_prefix is the first 8 hex chars — handy for revoke commands.
    """
    raw = generate_raw_token()
    h = hash_token(raw)
    db.create_token(conn, caller_id, h)
    return raw, h[:8]


def verify_bearer(
    authorization: str, conn: sqlite3.Connection
) -> Caller | None:
    """Strip 'Bearer ', hash, look up. Return Caller if valid.

    Returns None if:
    - Header doesn't start with 'Bearer '
    - Token not found
    - Token revoked
    - Caller revoked
    Updates last_used_at on successful verification.
    """
    if not authorization.startswith("Bearer "):
        return None

    raw = authorization[7:]
    if not raw:
        return None

    h = hash_token(raw)
    token_row = db.get_token(conn, h)
    if token_row is None:
        return None
    if token_row["revoked_at"] is not None:
        return None

    caller_row = db.get_caller_by_id(conn, token_row["caller_id"])
    if caller_row is None:
        return None
    if caller_row["revoked_at"] is not None:
        return None

    db.update_last_used(conn, h)
    return Caller(**caller_row)
