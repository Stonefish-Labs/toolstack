"""Configuration for the broker control panel."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    bind_addr: str = "127.0.0.1:8780"
    broker_url: str = "http://127.0.0.1:8765"
    broker_token_file: Path = Path("/home/admin/.config/toolstack/tokens/broker-panel.token")
    username: str = "admin"
    password_hash_file: Path = Path("/home/admin/.config/toolstack/tokens/broker-panel-password.hash")
    session_secret_file: Path = Path("/home/admin/.config/toolstack/tokens/broker-panel-session.key")
    session_ttl_seconds: int = 12 * 60 * 60

    @property
    def bind_host(self) -> str:
        return self.bind_addr.rsplit(":", 1)[0]

    @property
    def bind_port(self) -> int:
        return int(self.bind_addr.rsplit(":", 1)[1])

    def broker_token(self) -> str:
        return self.broker_token_file.read_text(encoding="utf-8").strip()

    def password_hash(self) -> str:
        return self.password_hash_file.read_text(encoding="utf-8").strip()

    def session_secret(self) -> str:
        return self.session_secret_file.read_text(encoding="utf-8").strip()


def load_config() -> Config:
    return Config(
        bind_addr=os.environ.get("BROKER_PANEL_BIND_ADDR", "127.0.0.1:8780"),
        broker_url=os.environ.get("BROKER_PANEL_BROKER_URL", "http://127.0.0.1:8765"),
        broker_token_file=Path(
            os.environ.get(
                "BROKER_PANEL_BROKER_TOKEN_FILE",
                "/home/admin/.config/toolstack/tokens/broker-panel.token",
            )
        ),
        username=os.environ.get("BROKER_PANEL_USERNAME", "admin"),
        password_hash_file=Path(
            os.environ.get(
                "BROKER_PANEL_PASSWORD_HASH_FILE",
                "/home/admin/.config/toolstack/tokens/broker-panel-password.hash",
            )
        ),
        session_secret_file=Path(
            os.environ.get(
                "BROKER_PANEL_SESSION_SECRET_FILE",
                "/home/admin/.config/toolstack/tokens/broker-panel-session.key",
            )
        ),
        session_ttl_seconds=int(os.environ.get("BROKER_PANEL_SESSION_TTL_SECONDS", str(12 * 60 * 60))),
    )
