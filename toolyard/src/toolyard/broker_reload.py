"""Broker registry reload notification."""

from __future__ import annotations

from pathlib import Path

import httpx

from toolyard.config import Config


def notify_broker_reload(config: Config) -> None:
    if not config.broker_reload_url:
        return
    if not config.broker_reload_token_file:
        raise ValueError(
            "TOOLYARD_BROKER_RELOAD_URL is set but "
            "TOOLYARD_BROKER_RELOAD_TOKEN_FILE is missing"
        )
    token = _read_token(config.broker_reload_token_file)
    response = httpx.post(
        config.broker_reload_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()


def _read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"broker reload token file is empty: {path}")
    return token
