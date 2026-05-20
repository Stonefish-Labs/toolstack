"""Long-running toolyard daemon."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from toolyard.audit import ToolyardAuditLog
from toolyard.broker_reload import notify_broker_reload
from toolyard.config import Config, load_config
from toolyard.docker_driver import CLIDockerDriver, DockerDriver
from toolyard.lifecycle import down, up
from toolyard.registry import walk_tools
from toolyard.secrets import ConnectSecretResolver, ConnectSecretWriter, SecretResolver, SecretWriter
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

    def start(self) -> None:
        descs = [desc for desc in walk_tools(self.config.tools_dir) if desc.enabled]
        if any(desc.secrets for desc in descs) and self.resolver is None:
            self.config.require_connect()
            self.resolver = ConnectSecretResolver(
                self.config.op_connect_host or "",
                self.config.op_connect_token_file or "",
            )
        if any(desc.has_writable_secrets for desc in descs) and self.writer is None:
            self.config.require_write_connect()
            self.writer = ConnectSecretWriter(
                self.config.op_connect_host or "",
                self.config.op_connect_write_token_file or "",
            )

        for desc in descs:
            down(tool_id=desc.id, driver=self.driver)
            socket_dir: Path | None = None
            if desc.has_writable_secrets:
                assert self.writer is not None
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
            try:
                result = up(
                    descriptor=desc,
                    config=self.config,
                    driver=self.driver,
                    resolver=self.resolver,
                    write_socket_dir=socket_dir,
                )
            except Exception:
                if desc.id in self.servers:
                    self.servers.pop(desc.id).stop()
                raise
            self.started.append(desc.id)
            self.audit_log.record(
                "tool.started",
                tool_id=desc.id,
                container_id=result.container_id,
                image_id=result.image_id,
                healthy=result.healthy,
            )
        try:
            notify_broker_reload(self.config)
        except Exception as exc:
            print(f"warning: broker registry reload failed: {exc}", file=sys.stderr)

    def stop(self) -> None:
        for tool_id in reversed(self.started):
            down(tool_id=tool_id, driver=self.driver)
            self.audit_log.record("tool.stopped", tool_id=tool_id)
        self.started.clear()
        for server in self.servers.values():
            server.stop()
        self.servers.clear()


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
