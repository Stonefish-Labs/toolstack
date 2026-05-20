"""Configuration loading for the Discord Approver Bot.

Reads env vars and token files at startup, validates, and exposes a
single Settings object. Fails fast with clear errors on missing values.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Bot configuration, loaded from environment variables.

    Token files are read at load time — the Settings object holds the
    token values, not the file paths. This avoids keeping file handles
    or paths around after startup.
    """

    # Required: paths to token files (used at load time, then discarded)
    discord_token_file: Path = Field(alias="APPROVER_DISCORD_TOKEN_FILE")
    broker_token_file: Path = Field(alias="APPROVER_BROKER_TOKEN_FILE")
    broker_signing_secret_file: Path | None = Field(
        default=None, alias="APPROVER_BROKER_SIGNING_SECRET_FILE"
    )

    # Required: channel and broker URL
    discord_channel_id: int = Field(alias="APPROVER_DISCORD_CHANNEL_ID")
    broker_url: str = Field(alias="APPROVER_BROKER_URL")

    # Optional with defaults
    state_dir: Path = Field(default=Path("./state"), alias="APPROVER_STATE_DIR")
    poll_interval: float = Field(default=10.0, alias="APPROVER_POLL_INTERVAL_SECONDS")
    max_terminal_messages: int = Field(default=25, alias="APPROVER_MAX_TERMINAL_MESSAGES")
    allowed_user_ids_raw: str = Field(default="", alias="APPROVER_ALLOWED_USER_IDS")
    allowed_role_ids_raw: str = Field(default="", alias="APPROVER_ALLOWED_ROLE_IDS")

    # Resolved values (populated by model_validator)
    discord_token: str = ""
    broker_token: str = ""
    broker_signing_secret: str | None = None
    allowed_user_ids: frozenset[int] = frozenset()
    allowed_role_ids: frozenset[int] = frozenset()

    model_config = {
        "env_file": None,
        "populate_by_name": True,
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def read_token_files(self) -> "Settings":
        """Read token values from files and validate they're non-empty."""
        errors: list[str] = []

        for attr, file_field in [
            ("discord_token", "discord_token_file"),
            ("broker_token", "broker_token_file"),
        ]:
            path: Path = getattr(self, file_field)
            if not path.exists():
                errors.append(f"{file_field}: file not found at {path}")
                continue
            value = path.read_text().strip()
            if not value:
                errors.append(f"{file_field}: file at {path} is empty")
                continue
            object.__setattr__(self, attr, value)

        signing_path = self.broker_signing_secret_file
        if signing_path is not None:
            if not signing_path.exists():
                errors.append(
                    f"broker_signing_secret_file: file not found at {signing_path}"
                )
            else:
                value = signing_path.read_text().strip()
                if not value:
                    errors.append(
                        f"broker_signing_secret_file: file at {signing_path} is empty"
                    )
                else:
                    object.__setattr__(self, "broker_signing_secret", value)

        allowed_user_ids = _parse_id_set(
            self.allowed_user_ids_raw, "APPROVER_ALLOWED_USER_IDS", errors
        )
        allowed_role_ids = _parse_id_set(
            self.allowed_role_ids_raw, "APPROVER_ALLOWED_ROLE_IDS", errors
        )
        object.__setattr__(self, "allowed_user_ids", frozenset(allowed_user_ids))
        object.__setattr__(self, "allowed_role_ids", frozenset(allowed_role_ids))
        if not allowed_user_ids and not allowed_role_ids:
            errors.append(
                "configure at least one Discord allowlist entry via "
                "APPROVER_ALLOWED_USER_IDS or APPROVER_ALLOWED_ROLE_IDS"
            )

        if errors:
            raise ValueError(
                "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
            )
        return self


def _parse_id_set(raw: str, field_name: str, errors: list[str]) -> set[int]:
    ids: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit():
            errors.append(f"{field_name}: {item!r} is not a numeric Discord ID")
            continue
        ids.add(int(item))
    return ids


def load_config() -> Settings:
    """Load and validate configuration. Exits non-zero on errors."""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
