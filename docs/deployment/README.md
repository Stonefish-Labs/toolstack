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

Operator on tailnet
  -> optional Tailscale HTTPS
  -> 127.0.0.1:8780 broker-panel.service
       -> broker admin API on 127.0.0.1:8765

toolyardd.service
  -> reads /home/admin/.local/share/toolstack/tools/<id>/toolyard.yaml
  -> fetches initial secrets from Infisical
  -> injects secret files into container tmpfs at /run/secrets
  -> exposes per-tool writable-secret socket at /run/toolyard/secrets.sock
  -> exposes operator lifecycle control at /run/toolstack/toolyardd/control.sock
```

No Infisical credential is mounted into a tool container. Tool containers get only
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

- Infisical URL reachable from this VM
- Per-tool Infisical Universal Auth machine identities for project `ToolServer`
- Discord bot token and approval channel ID

## 1. Create XDG Config and State Layout

```bash
install -d -m 0700 /home/admin/.config/toolstack
install -d -m 0700 /home/admin/.config/toolstack/infisical
install -d -m 0700 /home/admin/.config/toolstack/tokens
install -d -m 0700 /home/admin/.local/state/toolstack/broker
install -d -m 0755 /home/admin/.local/share/toolstack/tools
```

Token files used by the deployment:

```
/home/admin/.config/toolstack/tokens/broker-approver.token
/home/admin/.config/toolstack/tokens/broker-approver-signing.key
/home/admin/.config/toolstack/tokens/broker-registry-admin.token
/home/admin/.config/toolstack/tokens/broker-panel.token
/home/admin/.config/toolstack/tokens/broker-panel-password.hash
/home/admin/.config/toolstack/tokens/broker-panel-session.key
/home/admin/.config/toolstack/tokens/agent-codex.token
/home/admin/.config/toolstack/tokens/discord-bot.token
```

Infisical machine identities live outside the broker token directory:

```
/home/admin/.config/toolstack/infisical/hello-rest.env
/home/admin/.config/toolstack/infisical/<tool-path>.env
```

Create token placeholders with mode `0600`, then paste values as they become
available:

```bash
for f in broker-approver broker-registry-admin broker-panel agent-codex discord-bot; do
  install -m 0600 /dev/null "/home/admin/.config/toolstack/tokens/$f.token"
done
openssl rand -hex 32 > /home/admin/.config/toolstack/tokens/broker-approver-signing.key
chmod 0600 /home/admin/.config/toolstack/tokens/broker-approver-signing.key
openssl rand -hex 32 > /home/admin/.config/toolstack/tokens/broker-panel-session.key
chmod 0600 /home/admin/.config/toolstack/tokens/broker-panel-session.key
```

## 2. Install Component Environments

```bash
cd /home/admin/toolstack
install -m 0600 docs/deployment/env/broker.env.example /home/admin/.config/toolstack/broker.env
install -m 0600 docs/deployment/env/toolyard.env.example /home/admin/.config/toolstack/toolyard.env
install -m 0600 docs/deployment/env/discord-approver.env.example /home/admin/.config/toolstack/discord-approver.env
install -m 0600 docs/deployment/env/broker-panel.env.example /home/admin/.config/toolstack/broker-panel.env
```

Edit the env files and set:

- `BROKER_PUBLIC_URL=https://broker.your-tailnet.ts.net`
- `BROKER_TOOLS_DIR` and `TOOLYARD_TOOLS_DIR` to the same tools root, usually `/home/admin/.local/share/toolstack/tools`
- `TOOLYARD_INFISICAL_HOST=<your Infisical URL>`
- `TOOLYARD_INFISICAL_ENVIRONMENT=<environment slug>`
- `APPROVER_DISCORD_CHANNEL_ID=<your Discord channel ID>`
- `APPROVER_ALLOWED_USER_IDS=<comma-separated Discord user IDs>` or `APPROVER_ALLOWED_ROLE_IDS=<comma-separated Discord role IDs>`
- `BROKER_APPROVER_SIGNING_SECRET_FILE` and `APPROVER_BROKER_SIGNING_SECRET_FILE` both point at `/home/admin/.config/toolstack/tokens/broker-approver-signing.key`
- `BROKER_PANEL_USERNAME=<admin username>` if you do not want the default `admin`

## 3. Install Python Venvs

```bash
cd /home/admin/toolstack
for d in broker toolyard discord-approver broker-panel; do
  cd "/home/admin/toolstack/$d"
  python3 -m venv .venv
  .venv/bin/pip install -e ".[dev]"
  .venv/bin/python -m pytest tests/ -q
done
```

## 4. Provision Infisical

Use project `ToolServer`. Toolyard interprets descriptor `vault` as the
Infisical project, `item` as the secret path, and `field` as the secret key.

For the first REST example, create path `/hello-rest` with secret `API_KEY`.
The `time-mcp` example has no secrets.

Create one local credentials file per Infisical path that the toolyard will
hydrate:

```bash
install -m 0600 /dev/null /home/admin/.config/toolstack/infisical/hello-rest.env
```

Each file contains the Universal Auth machine identity for that path:

```bash
INFISICAL_CLIENT_ID=...
INFISICAL_CLIENT_SECRET=...
```

Writable tools use the same per-path machine identity through `toolyardd`.
Containers request writable updates through their per-tool Unix socket; they do
not receive Infisical credentials.

## 5. Initialize Broker And Service Callers

```bash
cd /home/admin/toolstack/broker
.venv/bin/brokerctl init-db

.venv/bin/brokerctl create-caller --name svc.approver \
  --broker-op broker.approve \
  --broker-op broker.reject \
  --broker-op broker.list_requests \
  --broker-op broker.audit \
  --broker-op broker.approval_messages.read \
  --broker-op broker.approval_messages.write
# paste the printed token into /home/admin/.config/toolstack/tokens/broker-approver.token

.venv/bin/brokerctl create-caller --name svc.toolyard \
  --broker-op broker.registry.reload
# paste the printed token into /home/admin/.config/toolstack/tokens/broker-registry-admin.token

.venv/bin/brokerctl create-caller --name svc.broker-panel \
  --broker-op broker.admin.* \
  --broker-op broker.list_requests \
  --broker-op broker.audit
# paste the printed token into /home/admin/.config/toolstack/tokens/broker-panel.token

.venv/bin/brokerctl create-caller --name agent.codex
# paste the printed token into /home/admin/.config/toolstack/tokens/agent-codex.token
```

The token value is printed once. Keep each token file mode `0600`. The approver signing key is not a broker token; it is a separate HMAC secret shared only by broker and `discord-approver`.

Use Broker Panel to fill in the agent caller's tool permissions after the token
is created. The panel shows operation descriptions from each `toolyard.yaml` so
you can choose `allow`, `review`, or `deny` per operation.

Generate the broker panel password hash after the broker-panel venv is installed:

```bash
cd /home/admin/toolstack/broker-panel
.venv/bin/broker-panel hash-password 'replace-this-password' > /home/admin/.config/toolstack/tokens/broker-panel-password.hash
chmod 0600 /home/admin/.config/toolstack/tokens/broker-panel-password.hash
```

## 6. Install Systemd Units

```bash
sudo install -m 0644 /home/admin/toolstack/docs/deployment/systemd/broker.service /etc/systemd/system/broker.service
sudo install -m 0644 /home/admin/toolstack/docs/deployment/systemd/toolyardd.service /etc/systemd/system/toolyardd.service
sudo install -m 0644 /home/admin/toolstack/docs/deployment/systemd/discord-approver.service /etc/systemd/system/discord-approver.service
sudo install -m 0644 /home/admin/toolstack/docs/deployment/systemd/broker-panel.service /etc/systemd/system/broker-panel.service
sudo systemctl daemon-reload
```

Start in dependency order:

```bash
sudo systemctl enable --now broker.service
sudo systemctl enable --now toolyardd.service
sudo systemctl enable --now discord-approver.service
sudo systemctl enable --now broker-panel.service
```

## 7. Configure Tailscale Serve

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8765
```

Expose the broker panel on a separate tailnet name or port if desired:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8780
```

Verify locally and from another tailnet machine:

```bash
curl -s http://127.0.0.1:8765/v1/health
curl -s https://broker.your-tailnet.ts.net/v1/health
```

Both should return `{"ok": true}`.

## 8. First Tool Registration

The repo includes two public example tools:

- `/home/admin/toolstack/tools/hello-rest/toolyard.yaml`
- `/home/admin/toolstack/tools/time-mcp/toolyard.yaml`

The deployment tool root is outside the git checkout. Copy or maintain tools
there, then point both broker and toolyard at that same root:

```bash
install -d -m 0755 /home/admin/.local/share/toolstack/tools
cp -a /home/admin/toolstack/tools/hello-rest /home/admin/.local/share/toolstack/tools/
cp -a /home/admin/toolstack/tools/time-mcp /home/admin/.local/share/toolstack/tools/
```

`toolyardd.service` starts all enabled tools at boot. Use Broker Panel to choose
which callers can access each operation. If descriptor files are copied, moved,
or edited outside a Toolyard lifecycle command, use Broker Panel's "Reload Tool
Registry" action so the broker sees the current tool root. For container
lifecycle changes, use the Toolyard section in Broker Panel or the Toolyard CLI:

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
tail -n 50 /home/admin/.local/state/toolstack/toolyard-audit.jsonl
```
