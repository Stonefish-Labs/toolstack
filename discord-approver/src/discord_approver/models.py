"""Data models for the Discord Approver Bot.

These models match the broker's action_requests shape (see design/10-broker.md).
They use Pydantic for validation and JSON serialization, and are designed to
tolerate unknown fields from the broker (model_config extra = "ignore").
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class RequestStatus(str, Enum):
    """Status enum for action requests, matching the broker's status values."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DENIED = "denied"
    COMPLETED = "completed"
    FAILED = "failed"


class Request(BaseModel):
    """An action request from the broker.

    Fields match design/10-broker.md data model. Unknown fields from
    future broker versions are silently ignored.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    caller: str  # e.g. "agent.hermes"
    profile: str  # e.g. "home-default"
    tool: str  # e.g. "media"
    op: str  # e.g. "skip_track"
    arguments: dict = {}  # secrets already stripped by broker
    reason: str | None = None  # agent's stated reason
    status: RequestStatus = RequestStatus.PENDING_REVIEW
    risk: str = "write"  # "read" | "write" | "destructive"
    expires_at: int | None = None  # unix timestamp
    approver: str | None = None  # set after approve/reject
    decision_note: str | None = None  # approve note or reject reason


class StoredMessage(BaseModel):
    """A record of a Discord message posted for an action request."""

    request_id: int
    message_id: int
    last_status: str
    posted_at: int
    updated_at: int
