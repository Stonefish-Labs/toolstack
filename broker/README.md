# Toolstack Broker

The authority boundary of the toolserver system. It authenticates callers,
evaluates caller-owned policy, orchestrates human review through the Discord
bot, dispatches approved actions to tool servers, and audits everything.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

brokerctl init-db

brokerctl create-caller --name agent.hermes \
  --allow hello-rest.greet \
  --allow time-mcp.current_time \
  --review time-mcp.time_in

brokerctl create-caller --name svc.approver \
  --broker-op broker.approve \
  --broker-op broker.reject \
  --broker-op broker.list_requests \
  --broker-op broker.audit \
  --broker-op broker.approval_messages.read \
  --broker-op broker.approval_messages.write

brokerctl serve
```

Raw bearer tokens are printed once. Store them in `0600` files or the target
service's secret store.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BROKER_BIND_ADDR` | `127.0.0.1:8765` | HTTP listener |
| `BROKER_STATE_DIR` | `${XDG_STATE_HOME:-~/.local/state}/toolstack/broker` | SQLite database location |
| `BROKER_TOOLS_DIR` | `./tools` | directory containing `<tool>/toolyard.yaml` descriptors; deployment should set this to the same external root as `TOOLYARD_TOOLS_DIR` |
| `BROKER_APPROVAL_TIMEOUT_SECONDS` | `86400` | Pending to expired timeout |
| `BROKER_GRANT_DEFAULT_TTL_SECONDS` | `3600` | Default grant duration |
| `BROKER_ALLOW_UNKNOWN_TOOLS` | `false` | Accept unknown tool names in dev |
| `BROKER_PUBLIC_URL` | unset | Base URL for outbound links |
| `BROKER_DEFAULT_DISPATCHER` | `routing` | `routing` or `synthetic` |
| `BROKER_DISPATCH_TIMEOUT_SECONDS` | `30.0` | HTTP timeout when forwarding to tools |
| `BROKER_DISPATCH_HOST` | `127.0.0.1` | Host used to address local tools |
| `BROKER_APPROVER_SIGNING_SECRET_FILE` | unset | HMAC secret required for approval-capable callers when set |

## CLI Reference

```text
brokerctl init-db
brokerctl create-caller --name <name> [--allow TOOL.OP] [--review TOOL.OP] [--broker-op broker.OP] [--ttl seconds]
brokerctl refresh-token <caller-name>
brokerctl list-callers [--json] [--include-revoked]
brokerctl revoke-caller <name>
brokerctl list-tokens [--json] [--include-revoked]
brokerctl revoke-token <hash-prefix>
brokerctl list-requests [--status <status>] [--limit <n>] [--json]
brokerctl approve <id> --approver <name> [--note <text>]
brokerctl reject <id> --approver <name> [--reason <text>]
brokerctl audit [--after-id <id>] [--limit <n>] [--json]
brokerctl reload-registry
brokerctl serve [--bind host:port]
```

## Admin API Shape

The panel and automation use `/v1/admin/*` with a caller that has
`broker.admin.*`.

- `GET /v1/admin/tools` returns tool descriptors, including operation
  descriptions from `toolyard.yaml`.
- `POST /v1/admin/callers` creates one caller, an empty or supplied policy, and
  a one-time token.
- `GET /v1/admin/callers/<name>/policy` returns the caller's policy in
  structured per-tool/per-operation form.
- `PUT /v1/admin/callers/<name>/policy` replaces that caller policy.
- `POST /v1/admin/callers/<name>/refresh-token` revokes active tokens for the
  caller and prints a new one-time token.

Caller policy documents are stored in SQLite:

```json
{
  "tools": {
    "time-mcp": {
      "operations": {
        "current_time": "allow",
        "time_in": "review"
      }
    }
  },
  "broker_ops": ["broker.list_requests"],
  "auto_grant_ttl_seconds": 3600
}
```

## Dispatch

`HTTPDispatcher` forwards REST tool calls to
`http://<host>:<port>/v1/actions/<op>` with `arguments`, `reason`,
`broker_request_id`, and `caller: {"name": "<caller>"}`.

`MCPDispatcher` and `/mcp/<tool>` support MCP HTTP tools. For `tools/call`, the
broker evaluates the named operation through the same policy lifecycle. For
`tools/list`, `initialize`, and other non-call methods, the frame is forwarded
only if the caller policy enables at least one operation for that tool.

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```
