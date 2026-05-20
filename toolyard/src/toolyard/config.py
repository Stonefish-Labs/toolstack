"""Environment configuration for toolyard."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    op_connect_host: str | None = None
    op_connect_token_file: Path | None = None
    op_connect_write_token_file: Path | None = None
    tools_dir: Path = Path("./tools")
    state_dir: Path = Path("./state")
    runtime_dir: Path = Path("/run/toolstack/toolyardd")
    user_uid: int = 10000
    broker_reload_url: str | None = None
    broker_reload_token_file: Path | None = None

    @property
    def user(self) -> str:
        return f"{self.user_uid}:{self.user_uid}"

    def require_connect(self) -> None:
        missing = []
        if not self.op_connect_host:
            missing.append("TOOLYARD_OP_CONNECT_HOST")
        if not self.op_connect_token_file:
            missing.append("TOOLYARD_OP_CONNECT_TOKEN_FILE")
        if missing:
            raise ValueError("missing required Connect config: " + ", ".join(missing))

    def require_write_connect(self) -> None:
        self.require_connect()
        if not self.op_connect_write_token_file:
            raise ValueError(
                "missing required Connect write config: "
                "TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE"
            )


def load_config() -> Config:
    uid = int(os.environ.get("TOOLYARD_USER_UID", "10000"))
    if uid < 1:
        raise ValueError("TOOLYARD_USER_UID must be >= 1")

    def path_env(name: str) -> Path | None:
        value = os.environ.get(name)
        return Path(value) if value else None

    return Config(
        op_connect_host=os.environ.get("TOOLYARD_OP_CONNECT_HOST") or None,
        op_connect_token_file=path_env("TOOLYARD_OP_CONNECT_TOKEN_FILE"),
        op_connect_write_token_file=path_env("TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE"),
        tools_dir=Path(os.environ.get("TOOLYARD_TOOLS_DIR", "./tools")),
        state_dir=Path(os.environ.get("TOOLYARD_STATE_DIR", "./state")),
        runtime_dir=Path(os.environ.get("TOOLYARD_RUNTIME_DIR", "/run/toolstack/toolyardd")),
        user_uid=uid,
        broker_reload_url=os.environ.get("TOOLYARD_BROKER_RELOAD_URL") or None,
        broker_reload_token_file=path_env("TOOLYARD_BROKER_RELOAD_TOKEN_FILE"),
    )
