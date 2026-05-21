"""Password hashing and signed browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 16384, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p)
    return "$".join(
        [
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
        )
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


def sign_session(username: str, secret: str, ttl_seconds: int) -> str:
    expires_at = int(time.time()) + ttl_seconds
    message = f"{username}|{expires_at}"
    signature = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{message}|{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def verify_session(cookie: str | None, secret: str) -> str | None:
    if not cookie:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cookie.encode("ascii")).decode("utf-8")
        username, raw_expires, signature = decoded.split("|", 2)
        expires_at = int(raw_expires)
    except Exception:
        return None
    if expires_at < int(time.time()):
        return None
    message = f"{username}|{expires_at}"
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return username
