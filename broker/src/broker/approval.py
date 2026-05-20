"""Approval and rejection logic + grant creation.

Pure business logic. No FastAPI imports. Terminal states are immutable.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from broker import audit, db
from broker.dispatch import Dispatcher
from broker.lifecycle import _row_to_action_request
from broker.models import ActionRequest, RequestStatus, TERMINAL_STATUSES


async def approve_request(
    *,
    request_id: int,
    approver: str,
    note: str | None,
    conn: sqlite3.Connection,
    dispatcher: Any,
    config: Any,
) -> ActionRequest | None:
    """Approve a pending request, dispatch it, and return the updated request.

    If the request is not pending_review, returns the current state (no-op).
    Refuses to approve expired/terminal requests.
    """
    from broker import registry

    row = db.get_request(conn, request_id)
    if row is None:
        return None

    current_status = RequestStatus(row["status"])

    # Terminal or already-processed states → no-op
    if current_status in TERMINAL_STATUSES or current_status == RequestStatus.RUNNING:
        return _row_to_action_request(row, conn)

    # Only pending_review can be approved
    if current_status != RequestStatus.PENDING_REVIEW:
        return _row_to_action_request(row, conn)

    # Check if expired
    now = int(time.time())
    if row.get("expires_at") and row["expires_at"] < now:
        db.update_request_status(conn, request_id, status=RequestStatus.EXPIRED.value)
        audit.record(
            conn, "request.expired",
            request_id=request_id, caller_id=row["caller_id"],
            tool=row["tool"], op=row["op"],
        )
        return _row_to_action_request(
            db.get_request(conn, request_id), conn
        )

    # Record the approval
    db.record_approval(conn, request_id, approver, "approve", note)

    # Mark as approved (transient)
    db.update_request_status(conn, request_id, status=RequestStatus.APPROVED.value)
    audit.record(
        conn, "request.approved",
        request_id=request_id, caller_id=row["caller_id"],
        tool=row["tool"], op=row["op"],
        detail={"approver": approver, "note": note},
    )

    # Create grant if policy specified a TTL
    policy_dec = json.loads(row["policy_decision"]) if row.get("policy_decision") else {}
    ttl = policy_dec.get("grant_ttl_seconds")
    if ttl and ttl > 0:
        db.create_grant(
            conn, row["caller_id"], row["tool"], row["op"],
            expires_at=now + ttl,
        )

    # Dispatch
    req_model = _row_to_action_request(db.get_request(conn, request_id), conn)
    tool_desc = registry.get_tool(row["tool"])

    dispatch_result = await dispatcher.dispatch(req_model, tool_desc)

    if dispatch_result.success:
        result_json = json.dumps(dispatch_result.result) if dispatch_result.result else None
        db.update_request_status(
            conn, request_id,
            status=RequestStatus.COMPLETED.value,
            result_json=result_json,
        )
        audit.record(
            conn, "request.completed",
            request_id=request_id, caller_id=row["caller_id"],
            tool=row["tool"], op=row["op"],
        )
    else:
        db.update_request_status(
            conn, request_id,
            status=RequestStatus.FAILED.value,
            error=dispatch_result.error,
        )
        audit.record(
            conn, "request.failed",
            request_id=request_id, caller_id=row["caller_id"],
            tool=row["tool"], op=row["op"],
            detail={"error": dispatch_result.error},
        )

    return _row_to_action_request(db.get_request(conn, request_id), conn)


async def reject_request(
    *,
    request_id: int,
    approver: str,
    reason: str | None,
    conn: sqlite3.Connection,
) -> ActionRequest | None:
    """Reject a pending request. Returns the updated request.

    If the request is not pending_review, returns the current state (no-op).
    """
    row = db.get_request(conn, request_id)
    if row is None:
        return None

    current_status = RequestStatus(row["status"])

    # Only pending_review can be rejected
    if current_status != RequestStatus.PENDING_REVIEW:
        return _row_to_action_request(row, conn)

    # Check if already expired
    now = int(time.time())
    if row.get("expires_at") and row["expires_at"] < now:
        db.update_request_status(conn, request_id, status=RequestStatus.EXPIRED.value)
        audit.record(
            conn, "request.expired",
            request_id=request_id, caller_id=row["caller_id"],
            tool=row["tool"], op=row["op"],
        )
        return _row_to_action_request(
            db.get_request(conn, request_id), conn
        )

    # Record the rejection
    db.record_approval(conn, request_id, approver, "reject", reason)

    # Mark as rejected
    db.update_request_status(conn, request_id, status=RequestStatus.REJECTED.value)
    audit.record(
        conn, "request.rejected",
        request_id=request_id, caller_id=row["caller_id"],
        tool=row["tool"], op=row["op"],
        detail={"approver": approver, "reason": reason},
    )

    return _row_to_action_request(db.get_request(conn, request_id), conn)
