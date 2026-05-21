# Tool Template

A tool is a self-contained folder under `tools/<id>/` containing a
`toolyard.yaml`, a Dockerfile or image reference, and the tool code.

## REST Example: hello-rest

```yaml
id: hello-rest
type: rest
description: "Hello-world REST tool"
enabled: true
entrypoint:
  build: .
  port: 5000
secrets:
  - name: api_key
    vault: ToolServer
    field: API_KEY
healthcheck:
  http: /health
operations:
  - op: greet
    risk: read
```

Infisical layout: project `ToolServer`, path `/hello-rest`, key `API_KEY`.

Tool code reads `/run/secrets/api_key`; it never talks to Infisical directly.

## MCP-HTTP Example: time-mcp

```yaml
id: time-mcp
type: mcp-http
description: "Time queries via MCP"
enabled: true
entrypoint:
  build: .
  port: 5100
operations:
  - op: current_time
    risk: read
  - op: time_in
    risk: read
```

No secrets are needed for this pure-compute tool.

## Writable Field Example

For OAuth refresh-token rotation:

```yaml
id: media
type: rest
entrypoint:
  build: .
  port: 5200
secrets:
  - { name: client_id, field: CLIENT_ID }
  - { name: client_secret, field: CLIENT_SECRET }
  - { name: refresh_token, field: REFRESH_TOKEN, writable: true }
```

Inside the container, update only the declared writable secret:

```bash
curl --unix-socket /run/toolyard/secrets.sock \
  -X POST http://toolyard/v1/secrets/refresh_token \
  -H 'Content-Type: application/json' \
  -d '{"value":"NEW_REFRESH_TOKEN","reason":"oauth refresh"}'
```

Toolyardd patches exactly project `ToolServer`, path `/<tool id>`, key
`REFRESH_TOKEN` unless `vault` or `item` override that target in the descriptor.

## Broker Registration

Reload the broker registry so it sees the new `toolyard.yaml`, then enable the
desired operations on each caller policy in Broker Panel:

```bash
curl -X POST -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/broker-registry-admin.token)" \
  http://127.0.0.1:8765/v1/registry/reload
```

## Agent Skill Pairing

Tools with a broad operation surface should usually get a thin agent skill that
calls broker actions instead of exposing every operation in the agent's default
context. Follow [`22-agent-skill-convention.md`](22-agent-skill-convention.md)
for the client-side pattern: a stable CLI wrapper, dependency-free broker
calls, caller token config, and no downstream credentials or service logic in
the skill bundle.
