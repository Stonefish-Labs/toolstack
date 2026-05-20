"""HMAC request signing for approver-to-broker calls."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

SIGNATURE_VERSION = "v1"
TIMESTAMP_HEADER = "X-Toolstack-Timestamp"
NONCE_HEADER = "X-Toolstack-Nonce"
SIGNATURE_HEADER = "X-Toolstack-Signature"


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def signature_base(
    method: str, target: str, timestamp: str, nonce: str, body: bytes
) -> str:
    return "\n".join(
        [
            method.upper(),
            target,
            timestamp,
            nonce,
            body_digest(body),
        ]
    )


def compute_signature(
    secret: str, method: str, target: str, timestamp: str, nonce: str, body: bytes
) -> str:
    payload = signature_base(method, target, timestamp, nonce, body).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def make_signature_headers(
    secret: str,
    method: str,
    target: str,
    body: bytes,
    *,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or secrets.token_urlsafe(16)
    signature = compute_signature(secret, method, target, timestamp, nonce, body)
    return {
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: f"{SIGNATURE_VERSION}={signature}",
    }
