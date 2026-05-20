"""Tests for tokens.py — generation, hashing, verification."""

from __future__ import annotations

from broker import db, tokens
from broker.models import Caller


def test_generate_raw_token_is_unique():
    t1 = tokens.generate_raw_token()
    t2 = tokens.generate_raw_token()
    assert t1 != t2
    assert len(t1) > 20  # URL-safe base64 of 32 bytes


def test_hash_token_deterministic():
    raw = "test-token-123"
    h1 = tokens.hash_token(raw)
    h2 = tokens.hash_token(raw)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_create_and_verify_roundtrip(tmp_db):
    caller = db.create_caller(tmp_db, "agent.test", "home-default")
    raw, prefix = tokens.create_token_for_caller(tmp_db, caller["id"])

    assert len(prefix) == 8

    result = tokens.verify_bearer(f"Bearer {raw}", tmp_db)
    assert result is not None
    assert isinstance(result, Caller)
    assert result.name == "agent.test"
    assert result.profile == "home-default"


def test_verify_invalid_token(tmp_db):
    result = tokens.verify_bearer("Bearer invalid-token", tmp_db)
    assert result is None


def test_verify_missing_bearer_prefix(tmp_db):
    result = tokens.verify_bearer("just-a-token", tmp_db)
    assert result is None


def test_verify_revoked_token(tmp_db):
    caller = db.create_caller(tmp_db, "agent.rev", "p")
    raw, prefix = tokens.create_token_for_caller(tmp_db, caller["id"])

    # Revoke the token
    h = tokens.hash_token(raw)
    db.revoke_token(tmp_db, h)

    result = tokens.verify_bearer(f"Bearer {raw}", tmp_db)
    assert result is None


def test_verify_revoked_caller(tmp_db):
    caller = db.create_caller(tmp_db, "agent.rc", "p")
    raw, _ = tokens.create_token_for_caller(tmp_db, caller["id"])

    # Revoke the caller
    db.revoke_caller(tmp_db, "agent.rc")

    result = tokens.verify_bearer(f"Bearer {raw}", tmp_db)
    assert result is None


def test_last_used_updated_on_verify(tmp_db):
    caller = db.create_caller(tmp_db, "agent.lu", "p")
    raw, _ = tokens.create_token_for_caller(tmp_db, caller["id"])

    h = tokens.hash_token(raw)
    before = db.get_token(tmp_db, h)
    assert before["last_used_at"] is None

    tokens.verify_bearer(f"Bearer {raw}", tmp_db)

    after = db.get_token(tmp_db, h)
    assert after["last_used_at"] is not None
