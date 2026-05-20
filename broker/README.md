# Toolserver Broker

The authority boundary of the toolserver system. Authenticates agents, evaluates
profile-driven policy, orchestrates human-in-the-loop approval via the Discord
bot, dispatches approved actions to tool servers, and audits everything.

## Quick Start

```bash
# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Initialize the database
brokerctl init-db

# Create callers
brokerctl create-caller --name agent.hermes --profile home-default
# → Save the printed bearer token!

brokerctl create-caller --name bot.approver --profile approver
# → Save this token for the Discord bot

# Start the broker
brokerctl serve
# → Listening on 127.0.0.1:8765

# Test with curl
curl -s -H "Authorization: Bearer $AGENT_TOKEN" \
  http://127.0.0.1:8765/v1/actions/hello-rest.greet \
  -d '{"arguments": {"name": "agent"}, "reason": "smoke test"}'
```

## Configuration

All settings via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `BROKER_BIND_ADDR` | `127.0.0.1:8765` | HTTP listener |
| `BROKER_STATE_DIR` | `./state` | SQLite database location |
| `BROKER_TOOLS_DIR` | `./tools` | toolyard.yaml file directory |
| `BROKER_POLICIES_DIR` | `./policies/profiles` | Per-profile YAML ACLs |
| `BROKER_APPROVAL_TIMEOUT_SECONDS` | `86400` | Pending → expired timeout |
| `BROKER_GRANT_DEFAULT_TTL_SECONDS` | `3600` | Default grant duration |
| `BROKER_ALLOW_UNKNOWN_TOOLS` | `false` | Accept unknown tool names (dev shortcut) |
| `BROKER_PUBLIC_URL` | (unset) | Base URL for outbound links |
| `BROKER_DEFAULT_DISPATCHER` | `routing` | `routing` (real HTTP+MCP) or `synthetic` (dev fallback) |
| `BROKER_DISPATCH_TIMEOUT_SECONDS` | `30.0` | HTTP timeout when forwarding to tools |
| `BROKER_DISPATCH_HOST` | `127.0.0.1` | Host used to address tool containers |
| `BROKER_APPROVER_SIGNING_SECRET_FILE` | (unset) | File containing HMAC secret required for approver-profile calls when set |

## CLI Reference

```
brokerctl init-db                                           # Create schema
brokerctl create-caller --name <name> --profile <profile>   # Issue token
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

## HTTP API

See [docs/design/10-broker.md](../docs/design/10-broker.md) for the full spec.

### Unauthenticated
- `GET /v1/health` → `{"ok": true}`

### Authenticated (Bearer token)
- `POST /v1/actions/<tool>.<op>` — invoke an action (REST or MCP-wrapped)
- `POST /mcp/<tool>` — JSON-RPC blind forwarder for MCP tools
- `GET /v1/requests` — list requests (requires `broker.list_requests`)
- `GET /v1/requests/<id>` — single request
- `POST /v1/requests/<id>/approve` — approve (requires `broker.approve`)
- `POST /v1/requests/<id>/reject` — reject (requires `broker.reject`)
- `GET /v1/audit` — audit events (requires `broker.audit`)
- `GET /v1/registry` — tool registry
- `POST /v1/registry/reload` — reload tools + policies (requires `broker.registry.reload`)

## Dispatch

The broker uses a `Dispatcher` protocol with three implementations:

- **`HTTPDispatcher`** — for `type: rest` tools. Forwards `arguments`, `reason`,
  `broker_request_id`, and `caller` to `http://<host>:<port>/v1/actions/<op>`.
- **`MCPDispatcher`** — for `type: mcp-http` tools, used when invoked via
  `/v1/actions/<tool>.<op>`. Wraps the action as a JSON-RPC `tools/call` frame.
- **`SyntheticDispatcher`** — dev stub. Returns synthetic results. Selected via
  `BROKER_DEFAULT_DISPATCHER=synthetic`.

`RoutingDispatcher` picks based on the descriptor's `type` from the registry.
`mcp-stdio` is reserved in the schema but returns "not yet supported" — the
toolyard's stdio→http adapter is a future slice.

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Current Status

**Real dispatch slice** — the broker forwards approved actions to real tool
containers via `HTTPDispatcher` (REST) and `MCPDispatcher` (MCP-HTTP). The
`/mcp/<tool>` blind-forwarder route is live for raw JSON-RPC clients. The
registry reader is hardened (atomic reload, `enabled: false` handling,
validation). `BROKER_DEFAULT_DISPATCHER=synthetic` remains available as a dev
fallback.

The Discord bot's `HTTPBrokerClient` works against this broker with its
bearer token and optional HMAC signing secret; the approval API shape is
unchanged.
