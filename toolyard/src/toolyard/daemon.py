"""Long-running toolyard daemon."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from toolyard.audit import ToolyardAuditLog
from toolyard.broker_reload import notify_broker_reload
from toolyard.config import Config, load_config
from toolyard.control import ToolyardControlServer
from toolyard.docker_driver import CLIDockerDriver, DockerDriver
from toolyard.lifecycle import ToolStatus, container_name, down, list_tools, up
from toolyard.registry import get_descriptor, walk_tools
from toolyard.secrets import InfisicalSecretResolver, SecretResolver, SecretWriter
from toolyard.write_proxy import WritableSecretProxy, WritableSecretUnixServer


class ToolyardDaemon:
    def __init__(
        self, *, config: Config, driver: DockerDriver,
        resolver: SecretResolver | None = None, writer: SecretWriter | None = None,
    ):
        self.config = config
        self.driver = driver
        self.resolver = resolver
        self.writer = writer
        self.servers: dict[str, WritableSecretUnixServer] = {}
        self.started: list[str] = []
        self.audit_log = ToolyardAuditLog(config.state_dir / "toolyard-audit.jsonl")
        self.control_server = ToolyardControlServer(
            socket_path=config.control_socket,
            daemon=self,
        )
        self._lock = threading.RLock()

    def start(self) -> None:
        descs = [desc for desc in walk_tools(self.config.tools_dir) if desc.enabled]
        self._ensure_secret_clients(descs)

        for desc in descs:
            self._start_descriptor(desc, force=True)
        try:
            notify_broker_reload(self.config)
        except Exception as exc:
            print(f"warning: broker registry reload failed: {exc}", file=sys.stderr)
        self.control_server.start()

    def stop(self) -> None:
        self.control_server.stop()
        with self._lock:
            for tool_id in reversed(self.started):
                down(tool_id=tool_id, driver=self.driver)
                self.audit_log.record("tool.stopped", tool_id=tool_id)
            self.started.clear()
            for server in self.servers.values():
                server.stop()
            self.servers.clear()

    def list_statuses(self) -> list[ToolStatus]:
        with self._lock:
            return list_tools(config=self.config, driver=self.driver)

    def logs(self, tool_id: str, *, tail: int = 100) -> str:
        if get_descriptor(self.config.tools_dir, tool_id) is None:
            raise FileNotFoundError(f"unknown tool: {tool_id}")
        return self.driver.logs(container_name(tool_id), tail=tail, follow=False)

    def control_tool(self, tool_id: str, action: str) -> dict:
        with self._lock:
            desc = get_descriptor(self.config.tools_dir, tool_id)
            if desc is None:
                raise FileNotFoundError(f"unknown tool: {tool_id}")
            if action == "start":
                status = self._status_for(tool_id)
                if status and status.running:
                    return {"action": action, "tool": status.to_dict()}
                result = self._start_descriptor(desc, force=True)
                self._notify_reload()
                status = self._status_for(tool_id)
                return {
                    "action": action,
                    "tool": status.to_dict() if status else {"id": tool_id, "running": True},
                    "result": result.__dict__,
                }
            if action == "stop":
                self._stop_tool(tool_id)
                self._notify_reload()
                status = self._status_for(tool_id)
                return {
                    "action": action,
                    "tool": status.to_dict() if status else {"id": tool_id, "running": False},
                }
            if action in {"restart", "rebuild"}:
                self._stop_tool(tool_id)
                result = self._start_descriptor(desc, force=True)
                self._notify_reload()
                status = self._status_for(tool_id)
                return {
                    "action": action,
                    "tool": status.to_dict() if status else {"id": tool_id, "running": True},
                    "result": result.__dict__,
                }
            raise ValueError(f"unsupported action: {action}")

    def _status_for(self, tool_id: str) -> ToolStatus | None:
        for status in list_tools(config=self.config, driver=self.driver):
            if status.id == tool_id:
                return status
        return None

    def _ensure_secret_clients(self, descs) -> None:
        if any(desc.secrets for desc in descs) and self.resolver is None:
            self.resolver = _infisical_store(self.config)
        if any(desc.has_writable_secrets for desc in descs) and self.writer is None:
            if isinstance(self.resolver, InfisicalSecretResolver):
                self.writer = self.resolver
            else:
                self.writer = _infisical_store(self.config)

    def _start_descriptor(self, desc, *, force: bool):
        if not desc.enabled:
            raise ValueError(f"{desc.id} is disabled")
        self._ensure_secret_clients([desc])
        if force:
            self._stop_tool(desc.id, audit_event=False)
        socket_dir = self._start_write_proxy(desc)
        try:
            result = up(
                descriptor=desc,
                config=self.config,
                driver=self.driver,
                resolver=self.resolver,
                write_socket_dir=socket_dir,
            )
        except Exception:
            self._stop_write_proxy(desc.id)
            raise
        if desc.id not in self.started:
            self.started.append(desc.id)
        self.audit_log.record(
            "tool.started",
            tool_id=desc.id,
            container_id=result.container_id,
            image_id=result.image_id,
            healthy=result.healthy,
        )
        return result

    def _stop_tool(self, tool_id: str, *, audit_event: bool = True) -> None:
        down(tool_id=tool_id, driver=self.driver)
        self._stop_write_proxy(tool_id)
        self.started = [started for started in self.started if started != tool_id]
        if audit_event:
            self.audit_log.record("tool.stopped", tool_id=tool_id)

    def _start_write_proxy(self, desc) -> Path | None:
        if not desc.has_writable_secrets:
            return None
        assert self.writer is not None
        self._stop_write_proxy(desc.id)
        socket_dir = self.config.runtime_dir / "sockets" / desc.id
        server = WritableSecretUnixServer(
            socket_dir=socket_dir,
            proxy=WritableSecretProxy(
                descriptor=desc,
                writer=self.writer,
                audit_log=self.audit_log,
            ),
        )
        server.start()
        self.servers[desc.id] = server
        return socket_dir

    def _stop_write_proxy(self, tool_id: str) -> None:
        server = self.servers.pop(tool_id, None)
        if server is not None:
            server.stop()

    def _notify_reload(self) -> None:
        try:
            notify_broker_reload(self.config)
        except Exception as exc:
            print(f"warning: broker registry reload failed: {exc}", file=sys.stderr)


def _infisical_store(config: Config) -> InfisicalSecretResolver:
    config.require_infisical()
    return InfisicalSecretResolver(
        host=config.infisical_host or "",
        credentials_dir=config.infisical_credentials_dir,
        environment=config.infisical_environment,
        organization_slug=config.infisical_organization_slug,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="toolyardd", description="Run enabled toolyard tools and writable secret proxies")
    parser.parse_args(argv)
    config = load_config()
    daemon = ToolyardDaemon(config=config, driver=CLIDockerDriver())
    stopping = False

    def _stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    daemon.start()
    try:
        while not stopping:
            time.sleep(0.5)
    finally:
        daemon.stop()


if __name__ == "__main__":
    main()
