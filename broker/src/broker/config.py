"""Configuration — env var loading + validation.

Loads all BROKER_* env vars with sensible defaults.
Fails fast on invalid values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Immutable broker configuration loaded from environment."""

    bind_addr: str = "127.0.0.1:8765"
    state_dir: Path = field(default_factory=lambda: Path("./state"))
    tools_dir: Path = field(default_factory=lambda: Path("./tools"))
    policies_dir: Path = field(default_factory=lambda: Path("./policies/profiles"))
    approval_timeout_seconds: int = 86400
    grant_default_ttl_seconds: int = 3600
    allow_unknown_tools: bool = False
    public_url: str | None = None
    default_dispatcher: str = "routing"      # "routing" (HTTP+MCP) or "synthetic" (dev fallback)
    dispatch_timeout_seconds: float = 30.0
    dispatch_host: str = "127.0.0.1"
    approver_signing_secret: str | None = None

    @property
    def db_path(self) -> Path:
        return self.state_dir / "broker.sqlite3"

    @property
    def bind_host(self) -> str:
        return self.bind_addr.rsplit(":", 1)[0]

    @property
    def bind_port(self) -> int:
        return int(self.bind_addr.rsplit(":", 1)[1])


def load_config() -> Config:
    """Load configuration from BROKER_* environment variables."""

    def _bool(val: str) -> bool:
        return val.lower() in ("true", "1", "yes")

    approver_signing_secret = None
    approver_signing_secret_file = os.environ.get("BROKER_APPROVER_SIGNING_SECRET_FILE")
    if approver_signing_secret_file:
        path = Path(approver_signing_secret_file)
        if not path.exists():
            raise ValueError(
                "BROKER_APPROVER_SIGNING_SECRET_FILE points to a missing file: "
                f"{path}"
            )
        approver_signing_secret = path.read_text(encoding="utf-8").strip()
        if not approver_signing_secret:
            raise ValueError("BROKER_APPROVER_SIGNING_SECRET_FILE is empty")

    raw = {
        "bind_addr": os.environ.get("BROKER_BIND_ADDR", "127.0.0.1:8765"),
        "state_dir": Path(os.environ.get("BROKER_STATE_DIR", "./state")),
        "tools_dir": Path(os.environ.get("BROKER_TOOLS_DIR", "./tools")),
        "policies_dir": Path(
            os.environ.get("BROKER_POLICIES_DIR", "./policies/profiles")
        ),
        "approval_timeout_seconds": int(
            os.environ.get("BROKER_APPROVAL_TIMEOUT_SECONDS", "86400")
        ),
        "grant_default_ttl_seconds": int(
            os.environ.get("BROKER_GRANT_DEFAULT_TTL_SECONDS", "3600")
        ),
        "allow_unknown_tools": _bool(
            os.environ.get("BROKER_ALLOW_UNKNOWN_TOOLS", "false")
        ),
        "public_url": os.environ.get("BROKER_PUBLIC_URL") or None,
        "default_dispatcher": os.environ.get("BROKER_DEFAULT_DISPATCHER", "routing"),
        "dispatch_timeout_seconds": float(
            os.environ.get("BROKER_DISPATCH_TIMEOUT_SECONDS", "30.0")
        ),
        "dispatch_host": os.environ.get("BROKER_DISPATCH_HOST", "127.0.0.1"),
        "approver_signing_secret": approver_signing_secret,
    }

    # Validate bind_addr format
    parts = raw["bind_addr"].rsplit(":", 1)
    if len(parts) != 2:
        raise ValueError(
            f"BROKER_BIND_ADDR must be host:port, got {raw['bind_addr']!r}"
        )
    try:
        port = int(parts[1])
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        raise ValueError(
            f"BROKER_BIND_ADDR port must be 1-65535, got {parts[1]!r}"
        )

    if raw["approval_timeout_seconds"] < 1:
        raise ValueError("BROKER_APPROVAL_TIMEOUT_SECONDS must be >= 1")

    if raw["grant_default_ttl_seconds"] < 0:
        raise ValueError("BROKER_GRANT_DEFAULT_TTL_SECONDS must be >= 0")

    if raw["default_dispatcher"] not in ("routing", "synthetic"):
        raise ValueError(
            f"BROKER_DEFAULT_DISPATCHER must be 'routing' or 'synthetic', "
            f"got {raw['default_dispatcher']!r}"
        )

    if raw["dispatch_timeout_seconds"] <= 0:
        raise ValueError("BROKER_DISPATCH_TIMEOUT_SECONDS must be > 0")

    return Config(**raw)
