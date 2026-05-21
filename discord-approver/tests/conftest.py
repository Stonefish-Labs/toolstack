"""Pytest fixtures shared across test modules."""

from __future__ import annotations

import pytest

from discord_approver.models import Request, RequestStatus


@pytest.fixture
def pending_request() -> Request:
    """A sample pending_review request."""
    return Request(
        id=1,
        caller="agent.hermes",
        tool="media",
        op="skip_track",
        arguments={"device_id": "abc-123", "direction": "forward"},
        reason="user asked me to skip",
        status=RequestStatus.PENDING_REVIEW,
        risk="write",
        expires_at=9999999999,
    )


@pytest.fixture
def approved_request() -> Request:
    """A sample approved request."""
    return Request(
        id=2,
        caller="agent.hermes",
        tool="media",
        op="skip_track",
        arguments={"device_id": "abc-123"},
        status=RequestStatus.APPROVED,
        risk="write",
        approver="testuser",
        decision_note="looks good",
    )


@pytest.fixture
def rejected_request() -> Request:
    """A sample rejected request."""
    return Request(
        id=3,
        caller="agent.hermes",
        tool="tasks",
        op="delete_project",
        arguments={"project_id": "999"},
        status=RequestStatus.REJECTED,
        risk="destructive",
        approver="testuser",
        decision_note="too dangerous",
    )


@pytest.fixture
def expired_request() -> Request:
    """A sample expired request."""
    return Request(
        id=4,
        caller="agent.codex",
        tool="calendar",
        op="create_event",
        arguments={"title": "standup"},
        status=RequestStatus.EXPIRED,
        risk="write",
    )


@pytest.fixture
def request_with_secrets() -> Request:
    """A request with sensitive argument fields that should be redacted."""
    return Request(
        id=5,
        caller="agent.hermes",
        tool="generic",
        op="call_api",
        arguments={
            "url": "https://api.example.com",
            "password": "secret123",
            "api_key": "sk-abc-456",
            "data": {"token": "tok-789", "name": "safe-value"},
        },
        status=RequestStatus.PENDING_REVIEW,
        risk="write",
    )
