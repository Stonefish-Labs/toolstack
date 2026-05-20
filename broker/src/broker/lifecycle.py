"""Request lifecycle — the state machine for action requests.

Pure business logic. No FastAPI imports. Testable with mock dispatcher + temp DB.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from broker import audit, db
from broker.dispatch import Dispatcher
from broker.models import (
    ActionRequest,
    Caller,
    Grant,
    PolicyInput,
    RequestStatus,
    ToolDescriptor,
)
from broker.policy import decide


def _row_to_action_request(row: dict, conn: sqlite3.Connection) -> ActionRequest:
    """Convert a DB row + joined caller info into an ActionRequest model."""
    caller_row = db.get_caller_by_id(conn, row["caller_id"])
    caller_name = caller_row["name"] if caller_row else "unknown"
    profile = caller_row["profile"] if caller_row else "unknown"

    policy_dec = json.loads(row["policy_decision"]) if row.get("policy_decision") else {}
    args = json.loads(row["args_json"]) if row.get("args_json") else {}
    result = json.loads(row["result_json"]) if row.get("result_json") else None

    # Get approver and decision_note from approvals table
    approvals = db.list_approvals_for_request(conn, row["id"])
    approver = approvals[-1]["approver"] if approvals else None
    decision_note = approvals[-1]["note"] if approvals else None

    return ActionRequest(
        id=row["id"],
        caller=caller_name,
        caller_id=row["caller_id"],
        profile=profile,
        tool=row["tool"],
        op=row["op"],
        arguments=args,
        reason=row.get("reason"),
        status=RequestStatus(row["status"]),
        risk=policy_dec.get("risk", "write"),
        policy_decision=policy_dec,
        result=result,
        error=row.get("error"),
        approver=approver,
        decision_note=decision_note,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row.get("expires_at"),
    )


async def handle_action_request(
    *,
    caller: Caller,
    tool: str,
    op: str,
    arguments: dict[str, Any],
    reason: str | None,
    conn: sqlite3.Connection,
    dispatcher: Dispatcher,
    config: Any,
) -> tuple[ActionRequest, dict | None]:
    """Process an incoming action request through the policy engine.

    Returns (request_record, immediate_result_or_None).
    - If allowed: dispatches synchronously, returns (request, result).
    - If review-required: creates pending record, returns (request, None).
    - If denied: marks denied, returns (request, None).
    """
    from broker import registry

    now = int(time.time())

    # 1. Check tool in registry (if not allowing unknown tools)
    tool_desc = registry.get_tool(tool)
    if tool_desc is None and not config.allow_unknown_tools:
        # Create denied request for audit trail
        policy_dec = {"effect": "deny", "reason": "unknown_tool", "risk": "write"}
        redacted_args = audit.redact_dict(arguments)
        row = db.create_request(
            conn,
            caller_id=caller.id,
            tool=tool,
            op=op,
            args_json=json.dumps(redacted_args),
            reason=reason,
            status=RequestStatus.DENIED.value,
            policy_decision=json.dumps(policy_dec),
        )
        audit.record(
            conn, "request.denied",
            request_id=row["id"], caller_id=caller.id, tool=tool, op=op,
            detail={"reason": "unknown_tool"},
        )
        return _row_to_action_request(row, conn), None

    # 2. Load active grants
    active_grant_rows = db.find_active_grants(conn, caller.id, tool, op, now)
    active_grants = [Grant(**g) for g in active_grant_rows]

    # 3. Evaluate policy
    policy_input = PolicyInput(
        caller_id=caller.id,
        profile=caller.profile,
        tool=tool,
        op=op,
        arguments=arguments,
        reason=reason,
        active_grants=active_grants,
    )
    decision = decide(policy_input)
    policy_dec_dict = decision.model_dump()

    # 4. Redact arguments before storage
    redacted_args = audit.redact_dict(arguments)

    # 5. Switch on decision
    if decision.effect == "allow":
        # Create request in running state
        row = db.create_request(
            conn,
            caller_id=caller.id,
            tool=tool,
            op=op,
            args_json=json.dumps(redacted_args),
            reason=reason,
            status=RequestStatus.RUNNING.value,
            policy_decision=json.dumps(policy_dec_dict),
        )
        audit.record(
            conn, "request.allowed",
            request_id=row["id"], caller_id=caller.id, tool=tool, op=op,
            detail={"reason": decision.reason},
        )

        # Create a grant if ttl specified and not from existing grant
        if decision.grant_ttl_seconds and decision.grant_ttl_seconds > 0:
            db.create_grant(
                conn, caller.id, tool, op,
                expires_at=now + decision.grant_ttl_seconds,
            )

        # Dispatch synchronously
        req_model = _row_to_action_request(row, conn)
        dispatch_result = await dispatcher.dispatch(req_model, tool_desc)

        if dispatch_result.success:
            result_json = json.dumps(dispatch_result.result) if dispatch_result.result else None
            row = db.update_request_status(
                conn, row["id"],
                status=RequestStatus.COMPLETED.value,
                result_json=result_json,
            )
            audit.record(
                conn, "request.completed",
                request_id=row["id"], caller_id=caller.id, tool=tool, op=op,
            )
            return _row_to_action_request(row, conn), dispatch_result.result
        else:
            row = db.update_request_status(
                conn, row["id"],
                status=RequestStatus.FAILED.value,
                error=dispatch_result.error,
            )
            audit.record(
                conn, "request.failed",
                request_id=row["id"], caller_id=caller.id, tool=tool, op=op,
                detail={"error": dispatch_result.error},
            )
            return _row_to_action_request(row, conn), None

    elif decision.effect == "review":
        expires_at = now + config.approval_timeout_seconds
        row = db.create_request(
            conn,
            caller_id=caller.id,
            tool=tool,
            op=op,
            args_json=json.dumps(redacted_args),
            reason=reason,
            status=RequestStatus.PENDING_REVIEW.value,
            policy_decision=json.dumps(policy_dec_dict),
            expires_at=expires_at,
        )
        audit.record(
            conn, "request.pending",
            request_id=row["id"], caller_id=caller.id, tool=tool, op=op,
            detail={"reason": decision.reason, "expires_at": expires_at},
        )
        return _row_to_action_request(row, conn), None

    else:  # deny
        row = db.create_request(
            conn,
            caller_id=caller.id,
            tool=tool,
            op=op,
            args_json=json.dumps(redacted_args),
            reason=reason,
            status=RequestStatus.DENIED.value,
            policy_decision=json.dumps(policy_dec_dict),
        )
        audit.record(
            conn, "request.denied",
            request_id=row["id"], caller_id=caller.id, tool=tool, op=op,
            detail={"reason": decision.reason},
        )
        return _row_to_action_request(row, conn), None


def get_request_model(conn: sqlite3.Connection, request_id: int) -> ActionRequest | None:
    """Load a request by ID and return as an ActionRequest model."""
    row = db.get_request(conn, request_id)
    if row is None:
        return None
    return _row_to_action_request(row, conn)


def list_request_models(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
    after_id: int | None = None,
) -> list[ActionRequest]:
    """List requests as ActionRequest models."""
    rows = db.list_requests(conn, status=status, limit=limit, after_id=after_id)
    return [_row_to_action_request(r, conn) for r in rows]
