"""toolyard operator CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from toolyard.broker_reload import notify_broker_reload
from toolyard.config import Config, load_config
from toolyard.docker_driver import CLIDockerDriver, DockerDriver
from toolyard.lifecycle import add, down, list_tools, restart, up
from toolyard.registry import get_descriptor, walk_tools
from toolyard.schema import load_descriptor
from toolyard.secrets import InfisicalSecretResolver, SecretResolver


DriverFactory = Callable[[], DockerDriver]
ResolverFactory = Callable[[Config, Sequence], SecretResolver | None]


def main(
    argv: list[str] | None = None,
    *,
    driver_factory: DriverFactory = CLIDockerDriver,
    resolver_factory: ResolverFactory | None = None,
) -> None:
    parser = argparse.ArgumentParser(prog="toolyard", description="Docker tool lifecycle runner")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("up"); p.add_argument("id", nargs="?")
    p = sub.add_parser("down"); p.add_argument("id", nargs="?")
    p = sub.add_parser("restart"); p.add_argument("id")
    p = sub.add_parser("add"); p.add_argument("folder")
    p = sub.add_parser("logs"); p.add_argument("id"); p.add_argument("--tail", type=int); p.add_argument("--follow", action="store_true")
    p = sub.add_parser("ls"); p.add_argument("--json", action="store_true", dest="as_json")
    p = sub.add_parser("validate"); p.add_argument("folder")
    p = sub.add_parser("secrets"); p.add_argument("id")

    args = parser.parse_args(argv)
    config = load_config()
    driver = driver_factory()
    resolver_factory = resolver_factory or _default_resolver

    try:
        if args.command == "validate":
            desc = load_descriptor(Path(args.folder))
            print(f"{Path(args.folder) / 'toolyard.yaml' if Path(args.folder).is_dir() else args.folder}: ok ({desc.id})")
        elif args.command == "secrets":
            _cmd_secrets(config, args.id)
        elif args.command == "add":
            desc = add(source_folder=Path(args.folder), config=config)
            print(f"added {desc.id} -> {config.tools_dir / desc.id}")
            _maybe_notify_reload(config)
        elif args.command == "up":
            descs = _select(config, args.id)
            resolver = resolver_factory(config, descs)
            for desc in descs:
                result = up(descriptor=desc, config=config, driver=driver, resolver=resolver)
                state = "healthy" if result.healthy else ("started" if result.healthy is None else "unhealthy")
                print(f"{desc.id}: {state} on 127.0.0.1:{result.host_port}")
            _maybe_notify_reload(config)
        elif args.command == "down":
            ids = [args.id] if args.id else [d.id for d in walk_tools(config.tools_dir)]
            for tool_id in ids:
                down(tool_id=tool_id, driver=driver)
                print(f"{tool_id}: stopped")
            _maybe_notify_reload(config)
        elif args.command == "restart":
            desc = _require(config, args.id)
            resolver = resolver_factory(config, [desc])
            result = restart(descriptor=desc, config=config, driver=driver, resolver=resolver)
            print(f"{desc.id}: restarted on 127.0.0.1:{result.host_port}")
            _maybe_notify_reload(config)
        elif args.command == "logs":
            print(driver.logs(f"toolyard-{args.id}", tail=args.tail, follow=args.follow), end="")
        elif args.command == "ls":
            rows = list_tools(config=config, driver=driver)
            if args.as_json:
                print(json.dumps({"tools": [row.to_dict() for row in rows]}, indent=2))
            else:
                _print_table(rows)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _select(config: Config, tool_id: str | None):
    return [_require(config, tool_id)] if tool_id else [d for d in walk_tools(config.tools_dir) if d.enabled]


def _require(config: Config, tool_id: str):
    desc = get_descriptor(config.tools_dir, tool_id)
    if desc is None:
        raise FileNotFoundError(f"unknown tool: {tool_id}")
    return desc


def _default_resolver(config: Config, descriptors: Sequence) -> SecretResolver | None:
    if not any(d.secrets for d in descriptors):
        return None
    config.require_infisical()
    return InfisicalSecretResolver(
        host=config.infisical_host or "",
        credentials_dir=config.infisical_credentials_dir,
        environment=config.infisical_environment,
        organization_slug=config.infisical_organization_slug,
    )


def _cmd_secrets(config: Config, tool_id: str) -> None:
    desc = _require(config, tool_id)
    count = len(desc.secrets)
    label = "secret" if count == 1 else "secrets"
    print(f"{desc.id} declares {count} {label}:")
    for ref in desc.secrets:
        mode = "write" if ref.writable else "read"
        print(f"  {ref.name}  ->  {ref.vault}/{ref.item or desc.id}/{ref.field}  ({mode})")


def _print_table(rows) -> None:
    if not rows:
        print("no tools found")
        return
    print(f"{'ID':<24} {'Enabled':<8} {'Running':<8} {'Port':<7} {'Healthy':<8}")
    print("-" * 62)
    for row in rows:
        healthy = "n/a" if row.healthy is None else ("yes" if row.healthy else "no")
        print(f"{row.id:<24} {str(row.enabled):<8} {str(row.running):<8} {row.host_port:<7} {healthy:<8}")


def _maybe_notify_reload(config: Config) -> None:
    try:
        notify_broker_reload(config)
    except Exception as exc:
        print(f"warning: broker registry reload failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
