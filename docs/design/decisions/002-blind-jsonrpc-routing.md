# ADR 002: MCP forwarding is blind JSON-RPC routing

**Status**: Accepted (2026-05-16)

## Context

Some tool servers expose MCP. The broker can either:

1. Understand MCP semantics — parse `initialize`, `tools/list`, `tools/call`; aggregate tools from multiple servers behind a single `/mcp` endpoint.
2. Blind-forward JSON-RPC frames — the broker authenticates and routes, but doesn't parse protocol structure.

The old `agent-broker/` chose option 1: a FastMCP-style `/mcp` adapter that aggregated multiple MCP servers and re-published their tools.

## Decision

Blind JSON-RPC routing. The broker exposes one MCP endpoint per tool at `POST /mcp/<tool_id>`. The broker authenticates the bearer, identifies the target tool, and forwards the JSON-RPC frame to the tool server's MCP endpoint at `http://127.0.0.1:<port>/mcp`.

The broker peeks at `method` and (for `tools/call`) `params.name` for policy lookup and audit, but it does not validate, transform, or aggregate payloads.

## Consequences

- MCP protocol evolution does not require broker changes. New methods just route through.
- Agents address one MCP endpoint per tool, not a single aggregate. Slightly less ergonomic, but conceptually cleaner: each tool is its own MCP server.
- Audit records the operation name (parsed from `params.name` on `tools/call`). The broker is not a full MCP parser, only enough to drive policy and audit.
- If protocol-level transforms ever become useful (redacting arg fields, rate-limiting per method), they require a deliberate addition — not piggybacking on an existing parser.
- Less code in the broker.

## Alternatives considered

- **FastMCP-style aggregator**: nicer for agents (one endpoint, dynamic `tools/list` listing everything available). But it couples the broker to MCP protocol versions and adds parsing complexity. Aggregation can be layered on later as a thin transformer in front of the blind router, if home-lab use justifies it.
- **No MCP forwarding at all** (REST only): cuts off MCP tools entirely. Not viable; Tasks and similar tools depend on MCP.
- **Per-tool MCP-aware proxies that the broker delegates to**: pushes complexity sideways without removing it. Rejected.
