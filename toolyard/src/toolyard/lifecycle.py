"""Tool lifecycle operations."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from toolyard.config import Config
from toolyard.docker_driver import DockerDriver
from toolyard.healthcheck import wait_for_healthy
from toolyard.models import ToolDescriptor
from toolyard.registry import reload_index
from toolyard.schema import load_descriptor
from toolyard.secrets import SecretResolver, resolve_secrets


@dataclass
class UpResult:
    tool_id: str
    container_id: str
    image_id: str
    host_port: int
    healthy: bool | None


@dataclass
class ToolStatus:
    id: str
    enabled: bool
    running: bool
    healthy: bool | None
    host_port: int
    container_id: str | None = None
    image_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def container_name(tool_id: str) -> str:
    return f"toolyard-{tool_id}"


def up(
    *, descriptor: ToolDescriptor, config: Config, driver: DockerDriver,
    resolver: SecretResolver | None = None, write_socket_dir: Path | None = None,
) -> UpResult:
    _ensure_supported(descriptor)
    if not descriptor.enabled:
        raise ValueError(f"{descriptor.id} is disabled")

    image_id = _image_for(descriptor, driver)
    volumes: list[tuple[str, str, str]] = []
    tmpfs: list[str] = []
    entrypoint: str | None = None
    command = descriptor.entrypoint.command or None
    group_add: list[str] = []
    secret_values: dict[str, str] = {}

    if descriptor.secrets:
        if resolver is None:
            raise ValueError(f"{descriptor.id} declares secrets but no resolver was provided")
        secret_values = resolve_secrets(descriptor=descriptor, resolver=resolver)
        tmpfs.append("/run/secrets:rw,noexec,nosuid,nodev,size=1m")
        volumes.append((str(_wait_wrapper_path()), "/toolyard/wait-for-secrets", "ro"))
        entrypoint = "/bin/sh"
        real_command = command or driver.image_command(image_id)
        if not real_command:
            raise ValueError(
                f"{descriptor.id} declares secrets but no image command could be found"
            )
        command = ["/toolyard/wait-for-secrets", *real_command]

    if descriptor.has_writable_secrets:
        if write_socket_dir is None:
            raise ValueError(
                f"{descriptor.id} declares writable secrets; start it via toolyardd "
                "or provide a write proxy socket"
            )
        volumes.append((str(write_socket_dir), "/run/toolyard", "ro"))
        group_add.append(str(os.getgid()))

    cid = driver.run(
        name=container_name(descriptor.id),
        image=image_id,
        port_mapping=(descriptor.entrypoint.port, descriptor.entrypoint.port),
        volumes=volumes,
        env=dict(descriptor.env),
        user=config.user,
        cap_drop_all=True,
        read_only=True,
        tmpfs=tmpfs,
        entrypoint=entrypoint,
        command=command,
        group_add=group_add,
    )
    if secret_values:
        driver.copy_archive(
            container_name(descriptor.id),
            "/run/secrets",
            {**secret_values, ".ready": "1"},
            uid=config.user_uid,
            gid=config.user_uid,
        )
        secret_values.clear()
    healthy = (
        wait_for_healthy(host_port=descriptor.entrypoint.port, spec=descriptor.healthcheck)
        if descriptor.healthcheck else None
    )
    return UpResult(descriptor.id, cid, image_id, descriptor.entrypoint.port, healthy)


def down(*, tool_id: str, driver: DockerDriver) -> None:
    name = container_name(tool_id)
    driver.stop(name)
    driver.remove(name)


def restart(
    *, descriptor: ToolDescriptor, config: Config, driver: DockerDriver,
    resolver: SecretResolver | None = None, write_socket_dir: Path | None = None,
) -> UpResult:
    down(tool_id=descriptor.id, driver=driver)
    return up(
        descriptor=descriptor,
        config=config,
        driver=driver,
        resolver=resolver,
        write_socket_dir=write_socket_dir,
    )


def add(*, source_folder: Path, config: Config) -> ToolDescriptor:
    desc = load_descriptor(source_folder)
    target = config.tools_dir / desc.id
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{target} already exists")
    config.tools_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(source_folder.resolve(), target, target_is_directory=True)
    return desc


def list_tools(*, config: Config, driver: DockerDriver) -> list[ToolStatus]:
    running = {c.get("Names") or c.get("name"): c for c in driver.ps("toolyard-")}
    statuses = []
    for desc in reload_index(config.tools_dir).values():
        name = container_name(desc.id)
        record = running.get(name)
        statuses.append(
            ToolStatus(
                id=desc.id,
                enabled=desc.enabled,
                running=record is not None and record.get("State") != "exited",
                healthy=None,
                host_port=desc.entrypoint.port,
                container_id=(record or {}).get("Id"),
                image_id=(record or {}).get("image") or (record or {}).get("Image"),
            )
        )
    return statuses


def _image_for(descriptor: ToolDescriptor, driver: DockerDriver) -> str:
    if descriptor.entrypoint.build:
        base = descriptor.source_dir or Path.cwd()
        return driver.build(
            context=(base / descriptor.entrypoint.build).resolve(),
            tag=f"toolyard-{descriptor.id}:latest",
        )
    return driver.pull(descriptor.entrypoint.image or "")


def _ensure_supported(descriptor: ToolDescriptor) -> None:
    if descriptor.type == "mcp-stdio":
        raise NotImplementedError("mcp-stdio is not yet implemented")
    if descriptor.volumes:
        raise NotImplementedError("volumes are not yet implemented")
    if descriptor.network != "default":
        raise NotImplementedError("non-default Docker networks are not yet implemented")


def _wait_wrapper_path() -> Path:
    return Path(__file__).resolve().parent / "container" / "wait-for-secrets.sh"
