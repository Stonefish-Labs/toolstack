"""Domain models — Pydantic v2 models for the broker's data types.

Response shapes for ActionRequest must match the Discord bot's Request model:
  id, caller (str), profile, tool, op, arguments, reason, status, risk,
  expires_at, approver, decision_note
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


# ── Enums ────────────────────────────────────────────────────────────

class RequestStatus(str, Enum):
    """Status enum for action_requests rows."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"        # transient — resolves to running
    REJECTED = "rejected"
    EXPIRED = "expired"
    DENIED = "denied"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset({
    RequestStatus.REJECTED,
    RequestStatus.EXPIRED,
    RequestStatus.DENIED,
    RequestStatus.COMPLETED,
    RequestStatus.FAILED,
})


# ── Core models ─────────────────────────────────────────────────────

class Caller(BaseModel):
    """A registered caller (agent or bot)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    profile: str
    created_at: int
    revoked_at: int | None = None


class Grant(BaseModel):
    """A time-limited grant for a (caller, tool, op) tuple."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    caller_id: int
    tool: str
    op: str
    scope_json: str | None = None
    expires_at: int


class PolicyInput(BaseModel):
    """Input to the policy decision function."""

    caller_id: int
    profile: str
    tool: str
    op: str
    arguments: dict[str, Any] = {}
    reason: str | None = None
    active_grants: list[Grant] = []


class PolicyDecision(BaseModel):
    """Output from the policy decision function."""

    effect: Literal["allow", "review", "deny"]
    reason: str
    risk: str = "write"  # "read" | "write" | "destructive"
    grant_ttl_seconds: int | None = None


class DispatchResult(BaseModel):
    """Result from dispatching an action to a tool server."""

    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class ActionRequest(BaseModel):
    """An action request — the primary entity the bot and agents interact with.

    Field names match the Discord bot's Request model exactly.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    caller: str              # caller name (string), NOT caller_id
    profile: str
    tool: str
    op: str
    arguments: dict[str, Any] = {}
    reason: str | None = None
    status: RequestStatus
    risk: str = "write"
    expires_at: int | None = None
    approver: str | None = None
    decision_note: str | None = None
    # Extra fields for internal use (bot ignores these via extra="ignore")
    caller_id: int | None = None
    policy_decision: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


class AuditEvent(BaseModel):
    """An audit log entry."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    request_id: int | None = None
    caller_id: int | None = None
    tool: str | None = None
    op: str | None = None
    detail: dict[str, Any] | None = None
    created_at: int


class ToolDescriptor(BaseModel):
    """A tool registered via toolyard.yaml.

    Subset of the toolyard's full schema — only fields the broker needs
    for policy lookup and dispatcher routing.
    """

    id: str
    type: str = "rest"          # rest | mcp-http | mcp-stdio
    enabled: bool = True
    port: int | None = None
    operations: list[dict[str, Any]] = []
    risk_class_default: str = "write"
