"""HMAC request signing for privileged broker callers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping, MutableMapping

SIGNATURE_VERSION = "v1"
TIMESTAMP_HEADER = "X-Toolstack-Timestamp"
NONCE_HEADER = "X-Toolstack-Nonce"
SIGNATURE_HEADER = "X-Toolstack-Signature"
DEFAULT_SKEW_SECONDS = 300


class SignatureError(ValueError):
    """Raised when a request signature is missing or invalid."""


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


def verify_signature(
    secret: str,
    method: str,
    target: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    nonce_cache: MutableMapping[str, int] | None = None,
    now: int | None = None,
    skew_seconds: int = DEFAULT_SKEW_SECONDS,
) -> None:
    now = int(time.time()) if now is None else now
    timestamp = headers.get(TIMESTAMP_HEADER)
    nonce = headers.get(NONCE_HEADER)
    signature_header = headers.get(SIGNATURE_HEADER)

    if not timestamp or not nonce or not signature_header:
        raise SignatureError("missing signature headers")
    if not signature_header.startswith(f"{SIGNATURE_VERSION}="):
        raise SignatureError("unsupported signature version")

    try:
        timestamp_int = int(timestamp)
    except ValueError as exc:
        raise SignatureError("invalid timestamp") from exc

    if abs(now - timestamp_int) > skew_seconds:
        raise SignatureError("stale timestamp")

    if nonce_cache is not None:
        _prune_nonce_cache(nonce_cache, now)
        if nonce in nonce_cache:
            raise SignatureError("reused nonce")

    expected = compute_signature(secret, method, target, timestamp, nonce, body)
    supplied = signature_header.split("=", 1)[1]
    if not hmac.compare_digest(expected, supplied):
        raise SignatureError("invalid signature")

    if nonce_cache is not None:
        nonce_cache[nonce] = now + skew_seconds




def _prune_nonce_cache(nonce_cache: MutableMapping[str, int], now: int) -> None:
    expired = [nonce for nonce, expires_at in nonce_cache.items() if expires_at <= now]
    for nonce in expired:
        del nonce_cache[nonce]
