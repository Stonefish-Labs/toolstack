# Toolstack Deployment Guide

This is the start-here guide for deploying Toolstack on the current Ubuntu VM.
The deployment root is `/home/admin/toolstack`, operator config and tokens live
under `/home/admin/.config/toolstack`, and agents reach the broker through
Tailscale Serve at `https://broker.your-tailnet.ts.net`.

## End State

```
Agent on tailnet
  -> Tailscale HTTPS
  -> 127.0.0.1:8765 broker.service
       -> toolyard-managed containers on 127.0.0.1:<tool port>
       -> discord-approver.service for human approval

toolyardd.service
  -> reads tools/<id>/toolyard.yaml
  -> fetches initial secrets from 1Password Connect
  -> injects secret files into container tmpfs at /run/secrets
  -> exposes per-tool writable-secret socket at /run/toolyard/secrets.sock
```

No 1Password token is mounted into a tool container. Tool containers get only
their resolved secret values in tmpfs and, for tools with writable fields, a
per-tool Unix socket that can update only fields declared as `writable: true` in
that tool's descriptor.

## 0. Prerequisites

Target host prerequisites:

- Ubuntu 24.04
- Docker installed and active
- Tailscale installed and active
- `admin` is in the `docker` group

Operator-provided values:

- 1Password Connect URL reachable from this VM
- 1Password Connect read token for vault `ToolServer`
- 1Password Connect read+write token for vault `ToolServer`
- Discord bot token and approval channel ID

## 1. Create XDG Config Layout

```bash
install -d -m 0700 /home/admin/.config/toolstack
install -d -m 0700 /home/admin/.config/toolstack/tokens
```

Token files used by the deployment:

```
/home/admin/.config/toolstack/tokens/op-connect-read.token
/home/admin/.config/toolstack/tokens/op-connect-readwrite.token
/home/admin/.config/toolstack/tokens/broker-approver.token
/home/admin/.config/toolstack/tokens/broker-approver-signing.key
/home/admin/.config/toolstack/tokens/broker-registry-admin.token
/home/admin/.config/toolstack/tokens/agent-codex.token
/home/admin/.config/toolstack/tokens/discord-bot.token
```

Create token placeholders with mode `0600`, then paste values as they become
available:

```bash
for f in op-connect-read op-connect-readwrite broker-approver broker-registry-admin agent-codex discord-bot; do
  install -m 0600 /dev/null "/home/admin/.config/toolstack/tokens/$f.token"
done
openssl rand -hex 32 > /home/admin/.config/toolstack/tokens/broker-approver-signing.key
chmod 0600 /home/admin/.config/toolstack/tokens/broker-approver-signing.key
```

## 2. Install Component Environments

```bash
cd /home/admin/toolstack
install -m 0600 docs/deployment/env/broker.env.example /home/admin/.config/toolstack/broker.env
install -m 0600 docs/deployment/env/toolyard.env.example /home/admin/.config/toolstack/toolyard.env
install -m 0600 docs/deployment/env/discord-approver.env.example /home/admin/.config/toolstack/discord-approver.env
```

Edit the env files and set:

- `BROKER_PUBLIC_URL=https://broker.your-tailnet.ts.net`
- `TOOLYARD_OP_CONNECT_HOST=<your Connect URL>`
- `APPROVER_DISCORD_CHANNEL_ID=<your Discord channel ID>`
- `APPROVER_ALLOWED_USER_IDS=<comma-separated Discord user IDs>` or `APPROVER_ALLOWED_ROLE_IDS=<comma-separated Discord role IDs>`
- `BROKER_APPROVER_SIGNING_SECRET_FILE` and `APPROVER_BROKER_SIGNING_SECRET_FILE` both point at `/home/admin/.config/toolstack/tokens/broker-approver-signing.key`

## 3. Install Python Venvs

```bash
cd /home/admin/toolstack
for d in broker toolyard discord-approver; do
  cd "/home/admin/toolstack/$d"
  python3 -m venv .venv
  .venv/bin/pip install -e ".[dev]"
  .venv/bin/python -m pytest tests/ -q
done
```

## 4. Provision 1Password

Use vault `ToolServer`.

For the first REST example, create item `hello-rest` with field `API_KEY`.
The `time-mcp` example has no secrets.

Create two Connect tokens scoped to `ToolServer`:

- read-only token -> `/home/admin/.config/toolstack/tokens/op-connect-read.token`
- read+write token -> `/home/admin/.config/toolstack/tokens/op-connect-readwrite.token`

The read+write token stays on the host and is used only by `toolyardd`.
Containers request writable updates through their per-tool Unix socket.

## 5. Initialize Broker And Service Callers

```bash
cd /home/admin/toolstack/broker
.venv/bin/brokerctl init-db

.venv/bin/brokerctl create-caller --name svc.approver --profile approver
# paste the printed token into /home/admin/.config/toolstack/tokens/broker-approver.token

.venv/bin/brokerctl create-caller --name svc.toolyard --profile registry-admin
# paste the printed token into /home/admin/.config/toolstack/tokens/broker-registry-admin.token

.venv/bin/brokerctl create-caller --name agent.codex --profile home-default
# paste the printed token into /home/admin/.config/toolstack/tokens/agent-codex.token
```

The token value is printed once. Keep each token file mode `0600`. The approver signing key is not a broker token; it is a separate HMAC secret shared only by broker and `discord-approver`.

## 6. Install Systemd Units

```bash
sudo install -m 0644 /home/admin/toolstack/docs/deployment/systemd/broker.service /etc/systemd/system/broker.service
sudo install -m 0644 /home/admin/toolstack/docs/deployment/systemd/toolyardd.service /etc/systemd/system/toolyardd.service
sudo install -m 0644 /home/admin/toolstack/docs/deployment/systemd/discord-approver.service /etc/systemd/system/discord-approver.service
sudo systemctl daemon-reload
```

Start in dependency order:

```bash
sudo systemctl enable --now broker.service
sudo systemctl enable --now toolyardd.service
sudo systemctl enable --now discord-approver.service
```

## 7. Configure Tailscale Serve

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8765
```

Verify locally and from another tailnet machine:

```bash
curl -s http://127.0.0.1:8765/v1/health
curl -s https://broker.your-tailnet.ts.net/v1/health
```

Both should return `{"ok": true}`.

## 8. First Tool Registration

The first two example tools are already present:

- `/home/admin/toolstack/tools/hello-rest/toolyard.yaml`
- `/home/admin/toolstack/tools/time-mcp/toolyard.yaml`

They are enabled and policy-registered in `home-default` and `readonly`.
`toolyardd.service` starts all enabled tools at boot. For manual lifecycle work:

```bash
cd /home/admin/toolstack/toolyard
.venv/bin/toolyard ls
.venv/bin/toolyard logs hello-rest --tail 50
```

## 9. Smoke Tests

Registry:

```bash
curl -s -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/agent-codex.token)" \
  http://127.0.0.1:8765/v1/registry | jq '.tools | keys'
```

REST through broker:

```bash
curl -s -X POST http://127.0.0.1:8765/v1/actions/hello-rest.greet \
  -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/agent-codex.token)" \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"name":"codex"},"reason":"deployment smoke"}' | jq
```

MCP through broker:

```bash
curl -s -X POST http://127.0.0.1:8765/mcp/time-mcp \
  -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/agent-codex.token)" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"current_time","arguments":{}},"id":1}' | jq
```

Writable-secret socket contract from inside a writable tool container:

```bash
curl --unix-socket /run/toolyard/secrets.sock \
  -X POST http://toolyard/v1/secrets/refresh_token \
  -H "Content-Type: application/json" \
  -d '{"value":"NEW_REFRESH_TOKEN","reason":"oauth refresh"}'
```

This succeeds only when `refresh_token` is declared with `writable: true` in
that tool's `toolyard.yaml`.

## Day-2 Operations

Restart a component:

```bash
sudo systemctl restart broker.service
sudo systemctl restart toolyardd.service
sudo systemctl restart discord-approver.service
```

Reload broker registry manually:

```bash
curl -X POST -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/broker-registry-admin.token)" \
  http://127.0.0.1:8765/v1/registry/reload
```

Inspect toolyard audit events:

```bash
tail -n 50 /home/admin/toolstack/toolyard/state/toolyard-audit.jsonl
```
