"""Audit event recording with argument redaction.

Records every state transition with caller, tool, op, and detail.
Strips secret-shaped fields from detail before storage as a defense-in-depth measure.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from broker import db

# Fields matching these patterns are redacted before storage
_REDACT_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pass)"),
    re.compile(r"(?i)(token|bearer)"),
    re.compile(r"(?i)(secret)"),
    re.compile(r"(?i)(api[_-]?key)"),
    re.compile(r"(?i)(authorization|auth)"),
    re.compile(r"(?i)(credential|cred)"),
    re.compile(r"(?i)(private[_-]?key)"),
]

_REDACTED = "[REDACTED]"


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of data with secret-shaped field values redacted."""
    if not isinstance(data, dict):
        return data
    result = {}
    for key, value in data.items():
        if any(pattern.search(key) for pattern in _REDACT_PATTERNS):
            result[key] = _REDACTED
        elif isinstance(value, dict):
            result[key] = redact_dict(value)
        else:
            result[key] = value
    return result


def record(
    conn: sqlite3.Connection,
    kind: str,
    *,
    request_id: int | None = None,
    caller_id: int | None = None,
    tool: str | None = None,
    op: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict:
    """Insert an audit event. Redacts secret-shaped fields from detail."""
    clean_detail = redact_dict(detail) if detail else detail
    return db.record_audit(
        conn,
        kind,
        request_id=request_id,
        caller_id=caller_id,
        tool=tool,
        op=op,
        detail=clean_detail,
    )
