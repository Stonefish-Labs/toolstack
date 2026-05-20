"""Pydantic models for toolyard.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ToolType = Literal["rest", "mcp-http", "mcp-stdio"]
Risk = Literal["read", "write", "destructive"]


class SecretRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    vault: str = "ToolServer"
    item: str | None = None
    field: str
    writable: bool = False

    @model_validator(mode="after")
    def no_reserved_names(self) -> "SecretRef":
        reserved = {"_connect_token", ".ready"}
        if self.name in reserved or self.name.startswith("."):
            raise ValueError(f"secret name {self.name!r} is reserved")
        return self


class HealthcheckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    http: str
    interval_seconds: float = Field(default=30, ge=0)
    start_period_seconds: float = Field(default=10, ge=0)


class VolumeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    container: str
    mode: Literal["ro", "rw"] = "ro"


class EntrypointSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build: str | None = None
    image: str | None = None
    port: int = Field(ge=1, le=65535)
    command: list[str] = []

    @model_validator(mode="after")
    def one_image_source(self) -> "EntrypointSpec":
        if bool(self.build) == bool(self.image):
            raise ValueError("exactly one of entrypoint.build or entrypoint.image is required")
        return self


class OperationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: str
    risk: Risk = "write"
    redact_args: list[str] = []


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    type: ToolType
    description: str = ""
    enabled: bool = True
    entrypoint: EntrypointSpec
    secrets: list[SecretRef] = []
    env: dict[str, str] = {}
    volumes: list[VolumeSpec] = []
    network: Literal["default", "isolated", "host"] = "default"
    healthcheck: HealthcheckSpec | None = None
    risk_class_default: Risk = "write"
    operations: list[OperationSpec] = []
    source_dir: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def fill_secret_items(self) -> "ToolDescriptor":
        for ref in self.secrets:
            if ref.item is None:
                ref.item = self.id
        return self

    @property
    def has_writable_secrets(self) -> bool:
        return any(ref.writable for ref in self.secrets)
