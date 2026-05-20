"""Tests for audit.py — redaction and recording."""

from __future__ import annotations

import json

from broker import audit, db


def test_redact_password_field():
    data = {"username": "alice", "password": "s3cret", "action": "login"}
    result = audit.redact_dict(data)
    assert result["username"] == "alice"
    assert result["password"] == "[REDACTED]"
    assert result["action"] == "login"


def test_redact_api_key_field():
    data = {"api_key": "abc123", "url": "https://example.com"}
    result = audit.redact_dict(data)
    assert result["api_key"] == "[REDACTED]"
    assert result["url"] == "https://example.com"


def test_redact_token_field():
    data = {"bearer_token": "xyz", "name": "test"}
    result = audit.redact_dict(data)
    assert result["bearer_token"] == "[REDACTED]"


def test_redact_nested_dict():
    data = {"outer": {"secret_value": "hidden", "safe": "visible"}}
    result = audit.redact_dict(data)
    assert result["outer"]["secret_value"] == "[REDACTED]"
    assert result["outer"]["safe"] == "visible"


def test_redact_authorization_field():
    data = {"authorization": "Bearer xyz", "content": "ok"}
    result = audit.redact_dict(data)
    assert result["authorization"] == "[REDACTED]"


def test_redact_case_insensitive():
    data = {"PASSWORD": "x", "Api_Key": "y", "SECRET": "z"}
    result = audit.redact_dict(data)
    assert result["PASSWORD"] == "[REDACTED]"
    assert result["Api_Key"] == "[REDACTED]"
    assert result["SECRET"] == "[REDACTED]"


def test_record_redacts_detail(tmp_db):
    """Secret-shaped fields in audit detail_json are redacted."""
    result = audit.record(
        tmp_db, "test.event",
        detail={"password": "s3cret", "tool": "media"},
    )
    events = db.list_audit_events(tmp_db)
    detail = json.loads(events[0]["detail_json"])
    assert detail["password"] == "[REDACTED]"
    assert detail["tool"] == "media"


def test_record_without_detail(tmp_db):
    result = audit.record(tmp_db, "simple.event", caller_id=1)
    assert result["kind"] == "simple.event"
    assert result["detail_json"] is None
