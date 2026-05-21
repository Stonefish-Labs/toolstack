"""SQLite database — schema + CRUD primitives.

Schema matches design/10-broker.md verbatim.
Uses stdlib sqlite3 with WAL journal mode.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS callers (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    created_at  INTEGER NOT NULL,
    revoked_at  INTEGER
);

CREATE TABLE IF NOT EXISTS caller_policies (
    caller_id                  INTEGER PRIMARY KEY REFERENCES callers(id) ON DELETE CASCADE,
    policy_json                TEXT NOT NULL,
    auto_grant_ttl_seconds     INTEGER,
    created_at                 INTEGER NOT NULL,
    updated_at                 INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    token_hash      TEXT PRIMARY KEY,
    caller_id       INTEGER NOT NULL REFERENCES callers(id),
    created_at      INTEGER NOT NULL,
    last_used_at    INTEGER,
    revoked_at      INTEGER
);

CREATE TABLE IF NOT EXISTS action_requests (
    id                  INTEGER PRIMARY KEY,
    caller_id           INTEGER NOT NULL REFERENCES callers(id),
    tool                TEXT NOT NULL,
    op                  TEXT NOT NULL,
    args_json           TEXT NOT NULL,
    reason              TEXT,
    status              TEXT NOT NULL,
    policy_decision     TEXT NOT NULL,
    result_json         TEXT,
    error               TEXT,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL,
    expires_at          INTEGER
);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES action_requests(id),
    approver    TEXT NOT NULL,
    action      TEXT NOT NULL,
    note        TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_messages (
    request_id   INTEGER PRIMARY KEY REFERENCES action_requests(id) ON DELETE CASCADE,
    surface      TEXT NOT NULL,
    message_id   INTEGER NOT NULL,
    last_status  TEXT NOT NULL,
    posted_at    INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    id          INTEGER PRIMARY KEY,
    caller_id   INTEGER NOT NULL REFERENCES callers(id),
    tool        TEXT NOT NULL,
    op          TEXT NOT NULL,
    scope_json  TEXT,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    request_id  INTEGER,
    caller_id   INTEGER,
    tool        TEXT,
    op          TEXT,
    detail_json TEXT,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_requests_status ON action_requests(status);
CREATE INDEX IF NOT EXISTS idx_action_requests_created ON action_requests(created_at);
CREATE INDEX IF NOT EXISTS idx_action_requests_expires ON action_requests(expires_at);
CREATE INDEX IF NOT EXISTS idx_grants_lookup ON grants(caller_id, tool, op, expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_created ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_tokens_caller ON tokens(caller_id);
CREATE INDEX IF NOT EXISTS idx_approval_messages_surface ON approval_messages(surface);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create tables if absent. Returns the connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(_SCHEMA)
    return conn


# ── Callers ──────────────────────────────────────────────────────────


def create_caller(conn: sqlite3.Connection, name: str) -> dict:
    """Insert a new caller. Returns the row as a dict."""
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO callers (name, created_at) VALUES (?, ?)",
        (name, now),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM callers WHERE id = ?", (cur.lastrowid,)
    ).fetchone())


def get_caller_by_id(conn: sqlite3.Connection, caller_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM callers WHERE id = ?", (caller_id,)).fetchone()
    return dict(row) if row else None


def get_caller_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM callers WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def revoke_caller(conn: sqlite3.Connection, name: str) -> bool:
    """Revoke a caller and all their tokens. Returns True if found."""
    now = int(time.time())
    cur = conn.execute(
        "UPDATE callers SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
        (now, name),
    )
    if cur.rowcount > 0:
        caller = get_caller_by_name(conn, name)
        if caller:
            conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE caller_id = ? AND revoked_at IS NULL",
                (now, caller["id"]),
            )
        conn.commit()
        return True
    return False


def list_callers(conn: sqlite3.Connection, include_revoked: bool = False) -> list[dict]:
    if include_revoked:
        rows = conn.execute("SELECT * FROM callers ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM callers WHERE revoked_at IS NULL ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Caller policies ─────────────────────────────────────────────────

def upsert_caller_policy(
    conn: sqlite3.Connection,
    caller_id: int,
    policy_json: str,
    auto_grant_ttl_seconds: int | None = None,
) -> dict:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO caller_policies (
            caller_id, policy_json, auto_grant_ttl_seconds, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(caller_id) DO UPDATE SET
            policy_json = excluded.policy_json,
            auto_grant_ttl_seconds = excluded.auto_grant_ttl_seconds,
            updated_at = excluded.updated_at
        """,
        (caller_id, policy_json, auto_grant_ttl_seconds, now, now),
    )
    conn.commit()
    return get_caller_policy(conn, caller_id) or {}


def get_caller_policy(conn: sqlite3.Connection, caller_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM caller_policies WHERE caller_id = ?", (caller_id,)
    ).fetchone()
    return dict(row) if row else None


def list_caller_policies(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM caller_policies ORDER BY caller_id"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Tokens ───────────────────────────────────────────────────────────

def create_token(conn: sqlite3.Connection, caller_id: int, token_hash: str) -> dict:
    """Store a hashed token."""
    now = int(time.time())
    conn.execute(
        "INSERT INTO tokens (token_hash, caller_id, created_at) VALUES (?, ?, ?)",
        (token_hash, caller_id, now),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM tokens WHERE token_hash = ?", (token_hash,)
    ).fetchone())


def get_token(conn: sqlite3.Connection, token_hash: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM tokens WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    return dict(row) if row else None


def revoke_token(conn: sqlite3.Connection, prefix_or_raw: str) -> int:
    """Revoke tokens matching a hash prefix. Returns count of revoked tokens."""
    now = int(time.time())
    # Try exact match first
    cur = conn.execute(
        "UPDATE tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (now, prefix_or_raw),
    )
    if cur.rowcount > 0:
        conn.commit()
        return cur.rowcount
    # Try prefix match
    cur = conn.execute(
        "UPDATE tokens SET revoked_at = ? WHERE token_hash LIKE ? AND revoked_at IS NULL",
        (now, prefix_or_raw + "%"),
    )
    conn.commit()
    return cur.rowcount


def revoke_tokens_for_caller(conn: sqlite3.Connection, caller_id: int) -> int:
    """Revoke all active tokens for a caller. Returns count revoked."""
    now = int(time.time())
    cur = conn.execute(
        "UPDATE tokens SET revoked_at = ? WHERE caller_id = ? AND revoked_at IS NULL",
        (now, caller_id),
    )
    conn.commit()
    return cur.rowcount


def list_tokens(conn: sqlite3.Connection, include_revoked: bool = False) -> list[dict]:
    if include_revoked:
        rows = conn.execute(
            "SELECT t.*, c.name as caller_name "
            "FROM tokens t JOIN callers c ON t.caller_id = c.id ORDER BY t.created_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT t.*, c.name as caller_name "
            "FROM tokens t JOIN callers c ON t.caller_id = c.id "
            "WHERE t.revoked_at IS NULL ORDER BY t.created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def update_last_used(conn: sqlite3.Connection, token_hash: str) -> None:
    now = int(time.time())
    conn.execute(
        "UPDATE tokens SET last_used_at = ? WHERE token_hash = ?",
        (now, token_hash),
    )
    conn.commit()


# ── Action requests ──────────────────────────────────────────────────

def create_request(
    conn: sqlite3.Connection,
    *,
    caller_id: int,
    tool: str,
    op: str,
    args_json: str,
    reason: str | None,
    status: str,
    policy_decision: str,
    expires_at: int | None = None,
) -> dict:
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO action_requests "
        "(caller_id, tool, op, args_json, reason, status, policy_decision, "
        "created_at, updated_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (caller_id, tool, op, args_json, reason, status, policy_decision,
         now, now, expires_at),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM action_requests WHERE id = ?", (cur.lastrowid,)
    ).fetchone())


def get_request(conn: sqlite3.Connection, request_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM action_requests WHERE id = ?", (request_id,)
    ).fetchone()
    return dict(row) if row else None


def update_request_status(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    status: str,
    result_json: str | None = None,
    error: str | None = None,
) -> dict | None:
    now = int(time.time())
    conn.execute(
        "UPDATE action_requests SET status = ?, result_json = ?, error = ?, "
        "updated_at = ? WHERE id = ?",
        (status, result_json, error, now, request_id),
    )
    conn.commit()
    return get_request(conn, request_id)


def list_requests(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
    after_id: int | None = None,
) -> list[dict]:
    conditions = []
    params: list[Any] = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if after_id is not None:
        conditions.append("id > ?")
        params.append(after_id)
    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM action_requests WHERE {where} ORDER BY id LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def find_expired_pending(conn: sqlite3.Connection, now: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM action_requests WHERE status = 'pending_review' "
        "AND expires_at IS NOT NULL AND expires_at < ?",
        (now,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Approvals ────────────────────────────────────────────────────────

def record_approval(
    conn: sqlite3.Connection,
    request_id: int,
    approver: str,
    action: str,
    note: str | None = None,
) -> dict:
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO approvals (request_id, approver, action, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (request_id, approver, action, note, now),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM approvals WHERE id = ?", (cur.lastrowid,)
    ).fetchone())


def list_approvals_for_request(conn: sqlite3.Connection, request_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM approvals WHERE request_id = ? ORDER BY created_at",
        (request_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Approval messages ────────────────────────────────────────────────

def upsert_approval_message(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    surface: str,
    message_id: int,
    last_status: str,
) -> dict:
    now = int(time.time())
    cur = conn.execute(
        """
        INSERT INTO approval_messages (
            request_id, surface, message_id, last_status, posted_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(request_id) DO UPDATE SET
            surface = excluded.surface,
            message_id = excluded.message_id,
            last_status = excluded.last_status,
            updated_at = excluded.updated_at
        """,
        (request_id, surface, message_id, last_status, now, now),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM approval_messages WHERE request_id = ?",
        (request_id,),
    ).fetchone())


def get_approval_message(
    conn: sqlite3.Connection, request_id: int
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM approval_messages WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return dict(row) if row else None


def list_approval_messages(
    conn: sqlite3.Connection, *, surface: str | None = None
) -> list[dict]:
    if surface is None:
        rows = conn.execute(
            "SELECT * FROM approval_messages ORDER BY request_id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM approval_messages WHERE surface = ? ORDER BY request_id",
            (surface,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_approval_message(conn: sqlite3.Connection, request_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM approval_messages WHERE request_id = ?",
        (request_id,),
    )
    conn.commit()
    return cur.rowcount > 0


# ── Grants ───────────────────────────────────────────────────────────

def create_grant(
    conn: sqlite3.Connection,
    caller_id: int,
    tool: str,
    op: str,
    expires_at: int,
    scope_json: str | None = None,
) -> dict:
    cur = conn.execute(
        "INSERT INTO grants (caller_id, tool, op, scope_json, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (caller_id, tool, op, scope_json, expires_at),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM grants WHERE id = ?", (cur.lastrowid,)
    ).fetchone())


def find_active_grants(
    conn: sqlite3.Connection,
    caller_id: int,
    tool: str,
    op: str,
    now: int,
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM grants WHERE caller_id = ? AND tool = ? AND op = ? "
        "AND expires_at > ? ORDER BY expires_at DESC",
        (caller_id, tool, op, now),
    ).fetchall()
    return [dict(r) for r in rows]


def purge_expired_grants(conn: sqlite3.Connection, now: int) -> int:
    cur = conn.execute("DELETE FROM grants WHERE expires_at < ?", (now,))
    conn.commit()
    return cur.rowcount


# ── Audit events ─────────────────────────────────────────────────────

def record_audit(
    conn: sqlite3.Connection,
    kind: str,
    *,
    request_id: int | None = None,
    caller_id: int | None = None,
    tool: str | None = None,
    op: str | None = None,
    detail: dict | None = None,
) -> dict:
    now = int(time.time())
    detail_json = json.dumps(detail) if detail else None
    cur = conn.execute(
        "INSERT INTO audit_events (kind, request_id, caller_id, tool, op, "
        "detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, request_id, caller_id, tool, op, detail_json, now),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM audit_events WHERE id = ?", (cur.lastrowid,)
    ).fetchone())


def list_audit_events(
    conn: sqlite3.Connection,
    *,
    after_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    conditions = []
    params: list[Any] = []
    if after_id is not None:
        conditions.append("id > ?")
        params.append(after_id)
    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM audit_events WHERE {where} ORDER BY id LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]
