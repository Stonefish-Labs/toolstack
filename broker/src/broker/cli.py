"""brokerctl — operator CLI for the broker.

Subcommands:
  init-db           Create the SQLite schema
  create-caller     Register a new caller and print the raw token
  list-callers      Show registered callers
  revoke-caller     Revoke a caller and all their tokens
  list-tokens       Show issued tokens
  revoke-token      Revoke a token by hash prefix
  list-requests     Show action requests
  approve           Approve a pending request (CLI-only, bypasses HTTP)
  reject            Reject a pending request (CLI-only, bypasses HTTP)
  audit             Show audit events
  reload-registry   Re-read toolyard.yaml files
  serve             Run the broker HTTP server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from broker import audit, db, policy, registry, tokens
from broker.config import load_config
from broker.dispatch import SyntheticDispatcher


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="brokerctl",
        description="Toolserver broker operator CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init-db
    sub.add_parser("init-db", help="Create the SQLite schema")

    # create-caller
    p = sub.add_parser("create-caller", help="Register a caller and print raw token")
    p.add_argument("--name", required=True, help="Caller name (e.g. agent.hermes)")
    p.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="TOOL.OP",
        help="Grant a tool operation without review; repeatable",
    )
    p.add_argument(
        "--review",
        action="append",
        default=[],
        metavar="TOOL.OP",
        help="Send a tool operation to approval review; repeatable",
    )
    p.add_argument(
        "--broker-op",
        action="append",
        default=[],
        metavar="broker.OP",
        help="Grant a broker control operation; repeatable",
    )
    p.add_argument("--ttl", type=int, default=None, help="Auto-grant TTL seconds")

    # refresh-token
    p = sub.add_parser("refresh-token", help="Revoke a caller's active tokens and issue a new one")
    p.add_argument("name", help="Caller name")

    # list-callers
    p = sub.add_parser("list-callers", help="Show registered callers")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--include-revoked", action="store_true")

    # revoke-caller
    p = sub.add_parser("revoke-caller", help="Revoke a caller and their tokens")
    p.add_argument("name", help="Caller name to revoke")

    # list-tokens
    p = sub.add_parser("list-tokens", help="Show issued tokens")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--include-revoked", action="store_true")

    # revoke-token
    p = sub.add_parser("revoke-token", help="Revoke a token by hash prefix")
    p.add_argument("prefix", help="Hash prefix or full hash of token to revoke")

    # list-requests
    p = sub.add_parser("list-requests", help="Show action requests")
    p.add_argument("--status", help="Filter by status")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true", dest="as_json")

    # approve
    p = sub.add_parser("approve", help="Approve a pending request")
    p.add_argument("request_id", type=int)
    p.add_argument("--approver", required=True)
    p.add_argument("--note", default=None)

    # reject
    p = sub.add_parser("reject", help="Reject a pending request")
    p.add_argument("request_id", type=int)
    p.add_argument("--approver", required=True)
    p.add_argument("--reason", default=None)

    # audit
    p = sub.add_parser("audit", help="Show audit events")
    p.add_argument("--after-id", type=int, default=None)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true", dest="as_json")

    # reload-registry
    sub.add_parser("reload-registry", help="Re-read toolyard.yaml files")

    # serve
    p = sub.add_parser("serve", help="Run the broker HTTP server")
    p.add_argument("--bind", default=None, help="Override BROKER_BIND_ADDR")

    args = parser.parse_args(argv)

    config = load_config()

    if args.command == "init-db":
        _cmd_init_db(config)
    elif args.command == "create-caller":
        _cmd_create_caller(config, args.name, args.allow, args.review, args.broker_op, args.ttl)
    elif args.command == "refresh-token":
        _cmd_refresh_token(config, args.name)
    elif args.command == "list-callers":
        _cmd_list_callers(config, args.as_json, args.include_revoked)
    elif args.command == "revoke-caller":
        _cmd_revoke_caller(config, args.name)
    elif args.command == "list-tokens":
        _cmd_list_tokens(config, args.as_json, args.include_revoked)
    elif args.command == "revoke-token":
        _cmd_revoke_token(config, args.prefix)
    elif args.command == "list-requests":
        _cmd_list_requests(config, args.status, args.limit, args.as_json)
    elif args.command == "approve":
        asyncio.run(_cmd_approve(config, args.request_id, args.approver, args.note))
    elif args.command == "reject":
        asyncio.run(_cmd_reject(config, args.request_id, args.approver, args.reason))
    elif args.command == "audit":
        _cmd_audit(config, args.after_id, args.limit, args.as_json)
    elif args.command == "reload-registry":
        _cmd_reload_registry(config)
    elif args.command == "serve":
        _cmd_serve(config, args.bind)


def _cmd_init_db(config):
    conn = db.init_db(config.db_path)
    conn.close()
    print(f"database initialized at {config.db_path}")


def _policy_from_cli(
    *,
    allow_ops: list[str],
    review_ops: list[str],
    broker_ops: list[str],
    ttl: int | None,
) -> dict:
    tools: dict[str, dict[str, dict[str, str]]] = {}
    for effect, values in (("allow", allow_ops), ("review", review_ops)):
        for value in values:
            try:
                tool, op = value.rsplit(".", 1)
            except ValueError:
                raise ValueError(f"tool operation must be TOOL.OP: {value}") from None
            tools.setdefault(tool, {"operations": {}})["operations"][op] = effect
    for broker_op in broker_ops:
        if not broker_op.startswith("broker."):
            raise ValueError(f"broker operation must start with broker.: {broker_op}")
    return policy.normalize_policy({
        "tools": tools,
        "broker_ops": broker_ops,
        "auto_grant_ttl_seconds": ttl,
    })


def _cmd_create_caller(
    config,
    name: str,
    allow_ops: list[str],
    review_ops: list[str],
    broker_ops: list[str],
    ttl: int | None,
):
    conn = db.init_db(config.db_path)
    try:
        caller_policy = _policy_from_cli(
            allow_ops=allow_ops,
            review_ops=review_ops,
            broker_ops=broker_ops,
            ttl=ttl,
        )
        caller_row = db.create_caller(conn, name)
        policy.upsert_policy(conn, caller_row["id"], caller_policy)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    raw_token, hash_prefix = tokens.create_token_for_caller(conn, caller_row["id"])
    audit.record(
        conn, "token.created",
        caller_id=caller_row["id"],
        detail={"caller": name, "hash_prefix": hash_prefix},
    )
    conn.close()

    print(f"caller created: {name} (id={caller_row['id']})")
    print()
    print("=" * 60)
    print("  BEARER TOKEN (save this — it will NOT be shown again):")
    print(f"  {raw_token}")
    print(f"  hash prefix: {hash_prefix}")
    print("=" * 60)


def _cmd_list_callers(config, as_json: bool, include_revoked: bool):
    conn = db.init_db(config.db_path)
    callers = db.list_callers(conn, include_revoked=include_revoked)
    conn.close()

    if as_json:
        print(json.dumps(callers, indent=2))
    else:
        if not callers:
            print("no callers found")
            return
        print(f"{'ID':<6} {'Name':<32} {'Revoked':<10}")
        print("-" * 52)
        for c in callers:
            revoked = "yes" if c.get("revoked_at") else "no"
            print(f"{c['id']:<6} {c['name']:<32} {revoked:<10}")


def _cmd_revoke_caller(config, name: str):
    conn = db.init_db(config.db_path)
    if db.revoke_caller(conn, name):
        audit.record(
            conn, "token.revoked",
            detail={"caller": name, "reason": "caller revoked via CLI"},
        )
        print(f"caller '{name}' and all tokens revoked")
    else:
        print(f"caller '{name}' not found or already revoked", file=sys.stderr)
    conn.close()


def _cmd_list_tokens(config, as_json: bool, include_revoked: bool):
    conn = db.init_db(config.db_path)
    toks = db.list_tokens(conn, include_revoked=include_revoked)
    conn.close()

    if as_json:
        print(json.dumps(toks, indent=2))
    else:
        if not toks:
            print("no tokens found")
            return
        print(f"{'Hash Prefix':<12} {'Caller':<32} {'Revoked':<10}")
        print("-" * 58)
        for t in toks:
            revoked = "yes" if t.get("revoked_at") else "no"
            prefix = t["token_hash"][:8]
            print(f"{prefix:<12} {t['caller_name']:<32} {revoked:<10}")


def _cmd_refresh_token(config, name: str):
    conn = db.init_db(config.db_path)
    caller = db.get_caller_by_name(conn, name)
    if caller is None or caller.get("revoked_at") is not None:
        print(f"caller '{name}' not found or revoked", file=sys.stderr)
        conn.close()
        sys.exit(1)
    revoked = db.revoke_tokens_for_caller(conn, caller["id"])
    raw_token, hash_prefix = tokens.create_token_for_caller(conn, caller["id"])
    audit.record(
        conn,
        "token.refreshed",
        caller_id=caller["id"],
        detail={"caller": name, "revoked": revoked, "hash_prefix": hash_prefix},
    )
    conn.close()

    print(f"caller token refreshed: {name} ({revoked} old token(s) revoked)")
    print()
    print("=" * 60)
    print("  BEARER TOKEN (save this — it will NOT be shown again):")
    print(f"  {raw_token}")
    print(f"  hash prefix: {hash_prefix}")
    print("=" * 60)


def _cmd_revoke_token(config, prefix: str):
    conn = db.init_db(config.db_path)
    count = db.revoke_token(conn, prefix)
    if count > 0:
        audit.record(
            conn, "token.revoked",
            detail={"hash_prefix": prefix[:8], "count": count},
        )
        print(f"revoked {count} token(s) matching '{prefix}'")
    else:
        print(f"no active tokens matching '{prefix}'", file=sys.stderr)
    conn.close()


def _cmd_list_requests(config, status: str | None, limit: int, as_json: bool):
    conn = db.init_db(config.db_path)
    rows = db.list_requests(conn, status=status, limit=limit)
    conn.close()

    if as_json:
        # Parse JSON fields for cleaner output
        for r in rows:
            if r.get("args_json"):
                r["arguments"] = json.loads(r["args_json"])
            if r.get("policy_decision"):
                r["policy"] = json.loads(r["policy_decision"])
        print(json.dumps(rows, indent=2))
    else:
        if not rows:
            print("no requests found")
            return
        print(f"{'ID':<6} {'Tool.Op':<30} {'Status':<18} {'CallerID':<10}")
        print("-" * 70)
        for r in rows:
            tool_op = f"{r['tool']}.{r['op']}"
            print(f"{r['id']:<6} {tool_op:<30} {r['status']:<18} {r['caller_id']:<10}")


async def _cmd_approve(config, request_id: int, approver: str, note: str | None):
    from broker.approval import approve_request

    conn = db.init_db(config.db_path)
    dispatcher = SyntheticDispatcher()
    result = await approve_request(
        request_id=request_id,
        approver=approver,
        note=note,
        conn=conn,
        dispatcher=dispatcher,
        config=config,
    )
    conn.close()

    if result is None:
        print(f"request {request_id} not found", file=sys.stderr)
        sys.exit(1)
    print(f"request {request_id}: {result.status.value}")
    if result.approver:
        print(f"  approver: {result.approver}")
    if result.decision_note:
        print(f"  note: {result.decision_note}")


async def _cmd_reject(config, request_id: int, approver: str, reason: str | None):
    from broker.approval import reject_request

    conn = db.init_db(config.db_path)
    result = await reject_request(
        request_id=request_id,
        approver=approver,
        reason=reason,
        conn=conn,
    )
    conn.close()

    if result is None:
        print(f"request {request_id} not found", file=sys.stderr)
        sys.exit(1)
    print(f"request {request_id}: {result.status.value}")
    if result.approver:
        print(f"  approver: {result.approver}")
    if result.decision_note:
        print(f"  reason: {result.decision_note}")


def _cmd_audit(config, after_id: int | None, limit: int, as_json: bool):
    conn = db.init_db(config.db_path)
    events = db.list_audit_events(conn, after_id=after_id, limit=limit)
    conn.close()

    if as_json:
        for e in events:
            if e.get("detail_json"):
                e["detail"] = json.loads(e["detail_json"])
        print(json.dumps(events, indent=2))
    else:
        if not events:
            print("no audit events found")
            return
        print(f"{'ID':<6} {'Kind':<25} {'ReqID':<8} {'Tool.Op':<25}")
        print("-" * 70)
        for e in events:
            tool_op = ""
            if e.get("tool") and e.get("op"):
                tool_op = f"{e['tool']}.{e['op']}"
            req_id = str(e.get("request_id") or "")
            print(f"{e['id']:<6} {e['kind']:<25} {req_id:<8} {tool_op:<25}")


def _cmd_reload_registry(config):
    tools = registry.load_registry(config.tools_dir)
    print(f"registry reloaded: {len(tools)} tool(s)")


def _cmd_serve(config, bind_override: str | None):
    import uvicorn
    from broker.api import create_app

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    host = config.bind_host
    port = config.bind_port
    if bind_override:
        parts = bind_override.rsplit(":", 1)
        host = parts[0]
        port = int(parts[1])

    app = create_app(config)

    print(f"starting broker on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
