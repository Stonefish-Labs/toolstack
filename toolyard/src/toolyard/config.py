"""Environment configuration for toolyard."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def default_state_dir() -> Path:
    """Return the XDG state directory for Toolyard runtime data."""
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "toolstack"


def default_config_dir() -> Path:
    """Return the XDG config directory for Toolstack host configuration."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home) if config_home else Path.home() / ".config"
    return root / "toolstack"


def default_infisical_credentials_dir() -> Path:
    return default_config_dir() / "infisical"


@dataclass(frozen=True)
class Config:
    infisical_host: str | None = None
    infisical_environment: str = "prod"
    infisical_credentials_dir: Path = field(default_factory=default_infisical_credentials_dir)
    infisical_organization_slug: str | None = None
    tools_dir: Path = Path("./tools")
    state_dir: Path = field(default_factory=default_state_dir)
    runtime_dir: Path = Path("/run/toolstack/toolyardd")
    control_socket: Path = Path("/run/toolstack/toolyardd/control.sock")
    user_uid: int = 10000
    broker_reload_url: str | None = None
    broker_reload_token_file: Path | None = None

    @property
    def user(self) -> str:
        return f"{self.user_uid}:{self.user_uid}"

    def require_infisical(self) -> None:
        missing = []
        if not self.infisical_host:
            missing.append("TOOLYARD_INFISICAL_HOST")
        if not self.infisical_environment:
            missing.append("TOOLYARD_INFISICAL_ENVIRONMENT")
        if missing:
            raise ValueError("missing required Infisical config: " + ", ".join(missing))


def load_config() -> Config:
    uid = int(os.environ.get("TOOLYARD_USER_UID", "10000"))
    if uid < 1:
        raise ValueError("TOOLYARD_USER_UID must be >= 1")
    runtime_dir = Path(os.environ.get("TOOLYARD_RUNTIME_DIR", "/run/toolstack/toolyardd"))

    def path_env(name: str) -> Path | None:
        value = os.environ.get(name)
        return Path(value) if value else None

    return Config(
        infisical_host=os.environ.get("TOOLYARD_INFISICAL_HOST") or None,
        infisical_environment=os.environ.get("TOOLYARD_INFISICAL_ENVIRONMENT", "prod"),
        infisical_credentials_dir=Path(
            os.environ.get(
                "TOOLYARD_INFISICAL_CREDENTIALS_DIR",
                default_infisical_credentials_dir(),
            )
        ),
        infisical_organization_slug=os.environ.get("TOOLYARD_INFISICAL_ORGANIZATION_SLUG") or None,
        tools_dir=Path(os.environ.get("TOOLYARD_TOOLS_DIR", "./tools")),
        state_dir=Path(os.environ.get("TOOLYARD_STATE_DIR", default_state_dir())),
        runtime_dir=runtime_dir,
        control_socket=Path(os.environ.get("TOOLYARD_CONTROL_SOCKET", runtime_dir / "control.sock")),
        user_uid=uid,
        broker_reload_url=os.environ.get("TOOLYARD_BROKER_RELOAD_URL") or None,
        broker_reload_token_file=path_env("TOOLYARD_BROKER_RELOAD_TOKEN_FILE"),
    )
