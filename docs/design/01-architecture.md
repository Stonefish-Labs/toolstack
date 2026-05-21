# Architecture

This document describes the four-component shape of the toolserver system and the trust boundaries between them. It assumes you have read [`00-principles.md`](00-principles.md) and the canonical essay at [`../trust-agents-with-action-not-access.md`](../trust-agents-with-action-not-access.md).

## Topology

```
              ┌────────────────────────────────────────────────────┐
              │  Agent host (Hermes / Codex / Claude / ...)        │
              │  Holds: broker bearer token (caller identity)      │
              └────────────────────────┬───────────────────────────┘
                                       │  HTTPS over Tailscale
                                       ▼
                               ┌───────────────┐
                               │ Tailscale     │  Serve HTTPS
                               │ Serve         │  tailnet-only
                               └───────┬───────┘
                                       │
                               ┌───────▼───────┐
                               │  Broker       │  127.0.0.1:NNNN
                               │  auth         │
                               │  policy       │
                               │  approval     │
                               │  forwarding   │
                               │  audit        │
                               └───┬───────┬───┘
                                   │       │
                ┌──────────────────┘       └──────────────────┐
                │ events / approve API           HTTP / JSON-RPC
                ▼                                             ▼
       ┌─────────────────┐                  ┌─────────────────────────────┐
       │  Discord bot    │                  │  Toolyard                   │
       │  (separate      │                  │  (Docker driver + registry) │
       │   process)      │                  │                             │
       └────────┬────────┘                  │  ┌───────────────────────┐  │
                │                           │  │ tool A  127.0.0.1:4501│  │
                ▼                           │  │ tool B  127.0.0.1:4502│  │
        (Discord channel)                   │  │ tool N  127.0.0.1:NNNN│  │
                                            │  └───────────────────────┘  │
                                            └──────────────┬──────────────┘
                                                           │ resolves secrets
                                                           │ at container start
                                                           ▼
                                              ┌─────────────────────┐
                                              │ Infisical           │
                                              │ (toolyard: per-path │
                                              │  machine identity   │
                                              │  to ToolServer)     │
                                              └─────────────────────┘
```

## The four components

### Broker

Role: authority boundary. The only thing the agent can address.

Owns:
- `callers`, `tokens` — agent identity, bearer tokens (hashed)
- `action_requests`, `approvals`, `grants` — request lifecycle
- `audit_events` — full action history
- The runtime registry of which `tool_id` resolves to which `127.0.0.1:port` (read from the configured tools root)

HTTP surface:
- `POST /v1/actions/<tool>.<op>` — request a REST action
- `POST /mcp/<tool>` — JSON-RPC blind-forward to a specific MCP tool (see [ADR 002](decisions/002-blind-jsonrpc-routing.md))
- `GET  /v1/requests` — list pending or historical requests
- `POST /v1/requests/<id>/approve` — Discord bot calls this
- `POST /v1/requests/<id>/reject`
- `GET  /v1/audit` — recent audit events

Storage: single SQLite at `${XDG_STATE_HOME:-~/.local/state}/toolstack/broker/broker.sqlite3`.

Target size: 500–800 LOC of Python.

The broker does NOT: execute tool code, resolve upstream secrets, parse MCP protocol beyond reading the method/op name for audit, host an HTML UI.

### Toolyard

Role: tool-server lifecycle, on-disk registry, and per-tool secret resolution.

Source of truth: `toolyard.yaml` files under the configured tools root. Each
definition specifies:

```yaml
id: media
type: rest               # rest | mcp-stdio | mcp-http
entrypoint:
  build: .               # or: image: ghcr.io/.../...
  port: 4502
secrets:
  - { name: client_id, field: CLIENT_ID }     # → /run/secrets/client_id in container
# optional, future use:
# volumes:
#   - host: /mnt/smb/foo
#     container: /data/foo
# network: isolated
```

Lifecycle commands:
- `toolyard up [id]` — start one or all (resolves secrets first)
- `toolyard down [id]`
- `toolyard restart <id>` — pull/rebuild, re-resolve secrets, restart
- `toolyard add <folder>` — adopt an existing folder containing a `toolyard.yaml`
- `toolyard logs <id>` — passthrough to `docker logs`
- `toolyard ls` — show registry + container status

Both the broker and toolyard read the same `toolyard.yaml` files. There is no separate registry service. The broker may cache the registry in memory and reload it on a SIGHUP or HTTP poke.

The toolyard is also the per-tool secrets boundary: it uses per-path Infisical
machine identities for the shared `ToolServer` project and injects only that
tool's fields into container tmpfs at startup. See [ADR 003](decisions/003-docker-sandboxing.md) for Docker and [`40-secrets.md`](40-secrets.md) for secrets handling.

### Discord Approver Bot

Role: human-in-the-loop interface for pending approvals.

For each `pending_review` request, the bot posts a channel message with:
- Caller, tool, operation, caller policy decision
- Argument summary (secrets stripped)
- Risk class from the policy decision
- Four buttons: **Approve** / **Approve+Note** / **Reject** / **Reject+Reason**

Discord modals collect the optional note / required reason. The bot calls the broker's approve/reject endpoint with the result. On state change (approved, rejected, timed-out, expired), the bot edits the original message to show the outcome.

Storage: no local persistent state. The bot records its `request_id ↔ message_id`
mapping through the broker.

See [ADR 006](decisions/006-discord-approval.md).

### Per-tool secrets

Not a service. A convention enforced by the toolyard.

At container start, the toolyard:
- Uses its own Infisical machine identity to read the relevant fields from the shared `ToolServer` project.
- Injects the resolved values into container tmpfs at `/run/secrets/<name>`.
- For writable fields, mounts only that tool's `/run/toolyard/secrets.sock` proxy path.

Tool code reads `/run/secrets/<name>`. Writable fields use `/run/toolyard/secrets.sock`; no Infisical credential is mounted in the container. Toolyardd is the per-tool scoping boundary inside the shared `ToolServer` project.

The broker is not in this path. The broker does not see, proxy, or cache upstream credentials.

See [ADR 004](decisions/004-secrets-at-workload.md) and [`40-secrets.md`](40-secrets.md).

## Trust boundaries

| From → To | Path | Auth |
|---|---|---|
| Agent → Broker | Tailscale Serve | Bearer token for one caller |
| Broker → Tool server | localhost HTTP/JSON-RPC | Optional shared secret per tool (defense in depth) |
| Toolyard → Infisical | HTTP to Infisical | Per-path Universal Auth machine identities kept on the host |
| Tool server → Infisical | none | Tool containers never receive Infisical credentials; writable fields go through toolyardd's per-tool Unix socket |
| Tool server → downstream API | Whatever the API requires | Credentials read from `/run/secrets/<name>` |
| Discord bot → Broker | localhost | Bot-specific broker token plus HMAC signing secret; Discord users are allowlisted by user ID and/or role ID before decisions are sent |
| Operator → Broker | CLI on broker host | Direct SQLite / `brokerctl` |

## Request lifecycles

### Happy path: auto-allowed read

1. Agent: `POST /v1/actions/media.get_playback_state` with bearer.
2. Broker: authenticate token → caller.
3. Broker: evaluate caller policy → allows tool `media`, op `get_playback_state` → allow.
4. Broker: look up `media` in registry → `http://127.0.0.1:4502`. Forward HTTP request.
5. Tool: executes (uses Media creds read from `/run/secrets/`). Returns JSON.
6. Broker: returns result to agent. Audit recorded.

### Approval path: write requiring review

1. Steps 1–2 same.
2. Broker: policy → review required for `media.skip_track`.
3. Broker: insert `action_requests` row with `pending_review`. Return `{status: "pending", request_id: ...}` to agent.
4. Discord bot picks up the pending request and posts an approval card.
5. Human: clicks **Approve+Note**. The bot verifies the Discord user/role allowlist, collects an optional note, and calls signed `POST /v1/requests/<id>/approve`.
6. Broker: marks approved, dispatches as in the happy path (steps 4–6).
7. Audit records approver + note.

### Denial path

1. Steps 1–2 same.
2. Broker: policy → deny, or token revoked. Return error. Audit denial. Bot not involved.

### Timeout path

1. Approval pending longer than `BROKER_APPROVAL_TIMEOUT_SECONDS` (default 24h).
2. Broker: marks `expired`. Cannot be approved retroactively.
3. Bot: on next poll, notices the expired status and edits the Discord message to "Timed out".

## Network topology

- **Tailscale VPN + Serve**: only path agents use to reach the broker. Tailscale Serve terminates HTTPS for `broker.<tailnet>.ts.net` and proxies to localhost.
- **Broker**: binds `127.0.0.1:NNNN` only. Not exposed beyond Tailscale Serve.
- **Tool containers**: bind `127.0.0.1:NNNN` only. Not reachable from anywhere except the broker host.
- **Infisical**: runs on the home network, reachable from the tool VM. Only the toolyard authenticates to it.

## Out of scope for v1

- Sandboxed one-shot job execution (the old `sandbox-job` backend type).
- Bulk approval (e.g., `/approve-all` slash command, batching, threading per request).
- Multi-tenant or multi-user broker.
- Off-host audit replication (JSONL export, log-pipeline integration).
- Heuristic risk classification at discovery time.
- mTLS between broker and tool containers, and between the Discord bot and broker. Localhost keeps this lower priority; the approver path uses HMAC signing as defense in depth until anything moves off-host.
- Agent-assisted catalog review or `brokerctl propose`-style flows.
- Web-based approval UI.

These are explicitly deferred, not forgotten. Current operation should follow
the active design docs.

## Storage summary

- **Broker**: `${XDG_STATE_HOME:-~/.local/state}/toolstack/broker/broker.sqlite3` — callers, tokens, action_requests, approvals, approval UI message mappings, grants, audit_events.
- **Toolyard**: filesystem-driven. `<tools-root>/<id>/toolyard.yaml` is canonical. secret values are injected into container tmpfs and are not persisted on the host. `${XDG_STATE_HOME:-~/.local/state}/toolstack/toolyard-audit.jsonl` records tool starts/stops and writable-secret updates.
- **Discord bot**: no local persistent state; it stores `request_id ↔ message_id` mappings through the broker.
- **Per-tool**: each tool owns its own data dir if persistence is needed (mounted volume per the tool's `toolyard.yaml`).

## Component sizes (target)

| Component | Target LOC | Old equivalent | Notes |
|---|---|---|---|
| Broker | 500–800 | ~2,500 (service+api+dispatcher+mcp_client+jobs+secrets+policy) | One process, no execution, no secrets |
| Toolyard | 300–500 | (none) | Docker driver + registry reader + secret resolver |
| Discord bot | 300–500 | (none) | Bot + modal + state-sync |
| Per-tool template | 50–150 | (none) | What a new tool needs to wire up |

Each component is independently deployable, replaceable, and testable.
