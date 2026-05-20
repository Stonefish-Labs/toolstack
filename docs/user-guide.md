# User Guide

How to use the toolserver day-to-day: call tools as an agent, add new tools,
manage tokens and policies, follow the approval flow, troubleshoot, and keep
the system humming.

This guide assumes the system is already installed per
[`deployment/README.md`](deployment/README.md).

## What this thing does

You have a broker on your tailnet. Agents (Hermes, pi-agent, Codex, etc.)
authenticate to it with a bearer token, request a *named action* on a *named
tool* (e.g., `hello-rest.greet` or `time-mcp.current_time`), and the broker:

1. Verifies the token → identifies the caller and its profile.
2. Evaluates policy → allow / require human review / deny.
3. If review: posts a card to Discord; waits for a human click.
4. If allowed: forwards to the tool's container (REST or JSON-RPC over MCP).
5. Returns the result, audits the whole transaction.

You — the operator — never give agents raw API keys. You give them broker
tokens scoped to profiles, and the broker decides what those profiles can do.

## Quickstart for agents

Every agent needs three things:

1. The broker URL (e.g., `https://broker.your-tailnet.ts.net`).
2. A bearer token (created with `brokerctl create-caller`).
3. The name of the tool + operation they want to invoke.

### REST tools (any HTTP client)

```bash
curl -X POST https://broker.your-tailnet.ts.net/v1/actions/hello-rest.greet \
  -H "Authorization: Bearer $BROKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"name": "agent"}, "reason": "user asked"}'
```

Responses:
- `200` — allowed and dispatched. Body has `result`.
- `202 {"request_id": N, "status": "pending_review"}` — needs approval. Poll `GET /v1/requests/N` for the outcome (or wait for the agent's logic to ask the user to check Discord).
- `403` — denied by policy.
- `404` — unknown tool/op.
- `502` — tool unreachable or failed.

### MCP tools (Codex, Claude Code, FastMCP clients)

Each MCP tool is at its own endpoint: `https://broker.your-tailnet.ts.net/mcp/<tool>`.

In Claude Code or Codex MCP config, register one entry per tool you want to use:

```json
{
  "mcpServers": {
    "time-mcp": {
      "url": "https://broker.your-tailnet.ts.net/mcp/time-mcp",
      "headers": {
        "Authorization": "Bearer $BROKER_TOKEN"
      }
    }
  }
}
```

The broker speaks blind JSON-RPC — protocol changes upstream don't require
broker updates. For `tools/call` the broker may return a JSON-RPC error with
`code: -32000` and `data.request_id` if approval is needed; the client should
poll `/v1/requests/<id>` for the outcome.

### Listing what's available

```bash
curl -s -H "Authorization: Bearer $BROKER_TOKEN" \
  https://broker.your-tailnet.ts.net/v1/registry | jq '.tools | keys'
```

## Day-to-day operator tasks

All `brokerctl` commands run on the toolserver VM as the `admin` user. If the
broker is running as a systemd service, you can either:

```bash
cd /home/admin/toolstack/broker && .venv/bin/brokerctl <cmd>
```

…or alias it for convenience:

```bash
echo 'alias brokerctl="/home/admin/toolstack/broker/.venv/bin/brokerctl"' >> ~/.bashrc
```

### Add an agent

```bash
brokerctl create-caller --name agent.hermes --profile home-default
```

The raw token is printed **once**. Distribute it to the agent immediately
(write to a file mode 0600, or paste into the agent's secrets store).

Profiles available out of the box:
- `home-default` — typical agent. Read access to listed tools, review for writes, deny for destructive.
- `readonly` — read-only across listed tools.
- `approver` - for the Discord bot; only allows approval/list/audit broker operations and may require HMAC signing. It cannot reload the registry. `registry-admin` is used by toolyardd only for `broker.registry.reload`. **Don't** give an agent either service profile.

To use a different policy shape, create a new profile under `broker/policies/profiles/<name>.yaml` and `brokerctl reload-registry` (or curl `/v1/registry/reload`).

### Revoke an agent's token

```bash
brokerctl list-tokens | grep agent.hermes
# Note the hash prefix
brokerctl revoke-token <hash-prefix>
```

Revoked tokens are rejected on the next request — no caching.

### Rotate an agent's token

```bash
brokerctl create-caller --name agent.hermes-v2 --profile home-default
# Hand the new token over, update the agent, verify it works
brokerctl revoke-token <old-hash-prefix>
```

If you must keep the same caller name, revoke the old token first (which
revokes the *token row*, not the caller), then issue a new one:

```bash
# Find the existing token hash for that caller
brokerctl list-tokens | grep agent.hermes
brokerctl revoke-token <old-hash-prefix>

# Issue a new one to the same caller (via the DB — there's no current CLI for "add token to existing caller")
# Easiest: create-caller with a v2 name. Or use SQL directly if you really must.
```

### See what agents are doing

```bash
# Last 20 audit events
brokerctl audit --limit 20

# Just pending approvals
brokerctl list-requests --status pending_review

# Anything that ran in the last hour for agent.hermes
brokerctl audit --limit 500 | grep agent.hermes
```

### Approve from CLI (if Discord is down)

```bash
brokerctl list-requests --status pending_review
brokerctl approve <request-id> --approver "me-via-cli" --note "Discord was down"
# or
brokerctl reject  <request-id> --approver "me-via-cli" --reason "denied: out of scope"
```

This is the same code path the Discord bot uses — see the audit trail for who
clicked what.

## Adding a tool

This is meant to be the easy part. The whole point of the toolyard is that
adding a tool should be "drop a folder, pick an entry point, run one command."

### Recipe

1. **Create the folder** under `tools/<id>/`:

   ```bash
   cd /home/admin/toolstack/tools
   mkdir my-tool && cd my-tool
   ```

2. **Write `toolyard.yaml`**. For a REST tool:

   ```yaml
   id: my-tool
   type: rest
   description: "Does the thing"
   enabled: true

   entrypoint:
     build: .
     port: 5300

   # Only if the tool needs secrets — leave out for pure-compute tools
   secrets:
     - { name: api_key, field: API_KEY }   # vault=ToolServer (default), item=my-tool (default)

   healthcheck:
     http: /health
     interval_seconds: 5
     start_period_seconds: 30

   operations:
     - { op: do_thing, risk: write }
     - { op: get_thing, risk: read }
   ```

   For an MCP tool, set `type: mcp-http` and have your tool expose `POST /mcp`
   speaking JSON-RPC. See `tools/time-mcp/` for a minimal example.

3. **Write a `Dockerfile`** (Python example):

   ```dockerfile
   FROM python:3.12-slim

   RUN useradd -u 10000 -m app
   WORKDIR /home/app

   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt

   COPY app.py .

   USER 10000
   CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5300"]
   ```

4. **Write the app code**. Read secrets from `/run/secrets/<name>` files — no
   1Password client library in the container:

   ```python
   def secret(name: str) -> str:
       with open(f"/run/secrets/{name}") as f:
           return f.read().strip()

   API_KEY = secret("api_key")
   ```

5. **Provision secrets in 1Password** (if the tool needs any). In the vault
   named in `toolyard.yaml` (default `ToolServer`), create item `my-tool` with field
   `API_KEY`.

6. **Bring it up**:

   ```bash
   sudo -u admin /home/admin/toolstack/toolyard/.venv/bin/toolyard up my-tool
   sudo -u admin /home/admin/toolstack/toolyard/.venv/bin/toolyard ls
   ```

7. **Test directly** (skip the broker for the first check):

   ```bash
   curl -X POST http://127.0.0.1:5300/v1/actions/do_thing \
     -H "Content-Type: application/json" \
     -d '{"arguments": {}}'
   ```

8. **Update broker policy** to allow agents to call it. Edit
   `broker/policies/profiles/home-default.yaml`:

   ```yaml
   allowed_tools:
     - my-tool          # add this
   allowed_ops:
     - "my-tool.get_*"  # auto-allow reads
   review_ops:
     - "my-tool.do_thing"  # require approval for writes
   ```

9. **Reload the broker**:

   ```bash
   curl -X POST http://127.0.0.1:8765/v1/registry/reload \
     -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/broker-admin.token)"
   ```

10. **Test through the broker**:

    ```bash
    curl -X POST https://broker.your-tailnet.ts.net/v1/actions/my-tool.get_thing \
      -H "Authorization: Bearer $AGENT_TOKEN" \
      -d '{"arguments": {}, "reason": "first call"}'
    ```

If you change tool code, `toolyard restart <id>` rebuilds the image and bounces
the container. If you change `toolyard.yaml`, the broker registry also needs a
reload (the toolyard pings it automatically via `TOOLYARD_BROKER_RELOAD_URL`).

### Tool checklist (cheat sheet)

- [ ] Folder under `tools/<id>/` with `id` lowercase a-z + digits + dashes
- [ ] `toolyard.yaml` validates (`toolyard validate tools/<id>`)
- [ ] Dockerfile runs as non-root (UID 10000 by default)
- [ ] Container listens on `0.0.0.0` (toolyard binds host-side to 127.0.0.1)
- [ ] Tool reads secrets from `/run/secrets/<name>`
- [ ] `/health` endpoint if `healthcheck.http` is declared
- [ ] `operations` lists each op with `risk: read|write|destructive`
- [ ] 1Password vault has an item with the right fields
- [ ] Broker `policies/profiles/<profile>.yaml` includes the tool
- [ ] Reload the broker after policy changes

## Approval flow

When policy says `review`, the agent gets a `202 pending_review` (or for MCP
clients, a JSON-RPC error `code: -32000`). The broker records the request.
The Discord bot polls the broker every few seconds; the next poll picks up the
pending request and posts an embed in the configured channel with four buttons:

- **Approve** — one-click. The agent's request continues.
- **Approve+Note** — approve with optional context (stored in audit).
- **Reject** — opens a modal for optional reason.
- **Reject+Reason** — opens a modal with **required** reason.

Rejection reasons are returned to the agent so it can adapt rather than retry blindly.

The Discord bot only honors button, modal, and `/clear` interactions from configured `APPROVER_ALLOWED_USER_IDS` or `APPROVER_ALLOWED_ROLE_IDS`. If HMAC signing is configured, the broker also requires valid signed approver requests.

Pending requests expire after `BROKER_APPROVAL_TIMEOUT_SECONDS` (default 24h).
Expired requests can't be retroactively approved.

### What if the bot is down?

`brokerctl approve <id>` / `brokerctl reject <id>` from the CLI does exactly
what the bot's button click does. Use this for fallback or for automated
approval flows (the audit trail records `approver` regardless of source).

### What if Discord is being noisy?

Each approved/rejected card stays in the channel as an audit trail. The bot
auto-prunes old terminal cards after `APPROVER_MAX_TERMINAL_MESSAGES`. There's
also a slash command to manually clear addressed cards — check `/clear` in the
configured channel. Approve/reject and `/clear` are limited to the configured
Discord user IDs and role IDs.

## Common operations

### Restart a tool after code change

```bash
toolyard restart my-tool
```

This rebuilds the image (if `entrypoint.build` is set), re-resolves secrets
from 1Password, and bounces the container. Takes 1-5 seconds depending on what
changed. The broker doesn't need restarting — the registry reload happens
automatically.

### Bring everything down

```bash
sudo systemctl stop discord-approver
sudo systemctl stop broker
sudo systemctl stop toolyard
# Toolyard stop also runs `toolyard down`, stopping all tool containers.
```

### Bring everything back up

```bash
sudo systemctl start toolyard    # tools first
sudo systemctl start broker
sudo systemctl start discord-approver
```

### See which containers are running

```bash
toolyard ls
# OR direct:
docker ps -f name=toolyard-
```

### Tail a tool's logs

```bash
toolyard logs my-tool --follow
# or
docker logs -f toolyard-my-tool
```

### Update a secret in 1Password

1. Edit the field in 1Password (e.g., `ToolServer/my-tool/API_KEY`).
2. `toolyard restart my-tool` — the toolyard re-resolves and rewrites the
   secret file; the tool reads the new value on startup.

No broker involvement, no token rotation.

### Rotate the toolyard's own Connect token

1. Generate a new Connect token in 1Password (same vault scope).
2. Replace `/home/admin/.config/toolstack/op-connect-read.token` (and write variant if any).
3. `sudo systemctl restart toolyard.service`.
4. Revoke the old token in the 1Password admin.

### Reload policies without restarting the broker

```bash
curl -X POST http://127.0.0.1:8765/v1/registry/reload \
  -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/broker-admin.token)"
```

Policies are reloaded automatically as part of this call, alongside the tool
registry. Edits to `policies/profiles/*.yaml` take effect immediately for new
requests; in-flight requests are unaffected.

## Wiring up specific agents

For tools with many operations, prefer a thin agent skill that calls broker
actions with broker-tool/profile config instead of putting every action in the
agent's always-loaded context. See
[`design/22-agent-skill-convention.md`](design/22-agent-skill-convention.md)
for the portable convention.

The skill should bootstrap profile config and token directories under the
Toolstack layout:

```text
<config-home>/toolstack/<broker-tool>/profiles/<profile>.env
<config-home>/toolstack/<broker-tool>/tokens/<profile>.token
```

Normal-use skill commands should be stable executables that work from any
current directory and call `/v1/actions/<tool>.<op>` directly. They should not
depend on shell profile state, local package installation, virtualenvs, or
downstream service credentials on the agent host.

### Hermes

Hermes already has a broker integration pattern from the previous (now retired)
`agent-broker`. The new broker uses the same HTTP contract for
`/v1/actions/<tool>.<op>` and the same response shapes, so Hermes should work
with no code changes — only the URL and token need updating.

```bash
# Issue a token
brokerctl create-caller --name agent.hermes --profile home-default

# In Hermes config (path varies):
broker_url: https://broker.your-tailnet.ts.net
broker_token_file: /etc/hermes/broker.token
```

### pi-agent

A pi-agent running on a Raspberry Pi on the tailnet works the same way:

```bash
# On the pi-agent host:
echo "$BROKER_TOKEN" > ~/.config/pi-agent/broker.token
chmod 600 ~/.config/pi-agent/broker.token
```

…and point its tool-invocation logic at `https://broker.your-tailnet.ts.net/v1/actions/`.

### Codex / Claude Code (MCP clients)

These speak MCP. Add one entry per tool to their MCP server config:

```json
{
  "mcpServers": {
    "time-mcp": {
      "url": "https://broker.your-tailnet.ts.net/mcp/time-mcp",
      "headers": {
        "Authorization": "Bearer $BROKER_TOKEN"
      }
    }
  }
}
```

Each MCP tool gets its own URL (intentional — see
[ADR 002](design/decisions/002-blind-jsonrpc-routing.md)). Restart your MCP
client after adding entries.

If a tool requires approval, the MCP client will see a JSON-RPC error of the
form:

```json
{
  "jsonrpc": "2.0",
  "id": ...,
  "error": {
    "code": -32000,
    "message": "pending_review",
    "data": {"request_id": 42, "status": "pending_review"}
  }
}
```

The client (or the user) should then poll `GET /v1/requests/42` until the
status is terminal, or just look at Discord and the result will appear in the
final state.

## Troubleshooting

### `401 invalid or revoked token`

The bearer doesn't match any non-revoked token in the broker DB.

```bash
brokerctl list-tokens | grep <caller-name>
# If revoked: revoked_at is not null. Issue a new token.
# If not present: did you save the raw token correctly? Tokens are 43-char URL-safe base64.
```

### `403 denied`

The token is valid, but the caller's profile doesn't allow this op.

```bash
# Find the caller's profile
brokerctl list-callers | grep <caller-name>

# Check the profile YAML
cat broker/policies/profiles/<profile>.yaml

# Is the tool in allowed_tools?
# Is the op in allowed_ops, review_ops, or denied_ops?
# Remember: denied takes precedence over allowed; allowed_ops takes precedence over review_ops.
```

If you change the profile, reload:

```bash
curl -X POST -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/broker-admin.token)" \
  http://127.0.0.1:8765/v1/registry/reload
```

### `404 unknown_tool`

The broker's registry doesn't have this tool.

```bash
# Is the tool in the registry?
curl -s -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/broker-admin.token)" \
  http://127.0.0.1:8765/v1/registry | jq '.tools | keys'

# Is the file there?
ls /home/admin/toolstack/tools/<id>/toolyard.yaml

# Did toolyard pick it up?
toolyard ls

# If a tool is enabled: false in toolyard.yaml, it won't appear in the broker registry.
```

After fixing, reload the broker registry.

### `502 tool_unreachable` or `tool_500`

The broker tried to forward to the tool's port, but the tool isn't responding.

```bash
toolyard ls
# If unhealthy or not listed, the container is down.

toolyard logs <id> --follow
# What's the tool saying?

# If the container is up but the broker can't reach it, check:
# - The broker config has BROKER_DISPATCH_HOST=127.0.0.1
# - The tool actually listens on 0.0.0.0 inside the container (not 127.0.0.1)
# - No firewall rules between the broker and localhost (rare on a single VM)
```

### Pending approval never resolves

```bash
# Is the Discord bot running?
systemctl status discord-approver

# Bot logs
journalctl -u discord-approver -n 50 --no-pager

# Did the card appear in the configured channel?
# Wrong channel ID? Verify APPROVER_DISCORD_CHANNEL_ID.
# User not allowed? Verify APPROVER_ALLOWED_USER_IDS / APPROVER_ALLOWED_ROLE_IDS.
# Bot lacks permission? It needs Send Messages + Embed Links + Manage Messages; /clear also needs Read Message History.
```

Worst case, approve from the CLI:

```bash
brokerctl list-requests --status pending_review
brokerctl approve <id> --approver "me-via-cli" --note "bot was down"
```

### `BROKER_DEFAULT_DISPATCHER=synthetic` for debugging

If you suspect the dispatcher is the problem, temporarily fall back to synthetic:

```bash
sudoedit /home/admin/.config/toolstack/broker.env
# Change: BROKER_DEFAULT_DISPATCHER=synthetic
sudo systemctl restart broker.service
```

All requests will return stub results without touching tool containers — useful
for isolating "is it the broker or the tool?" issues. Flip back when done.

### Audit trail isn't capturing something

The broker records audit events for every state transition. If you don't see
expected events:

```bash
brokerctl audit --limit 100 --json | jq '.events[] | .kind' | sort -u
```

Expected kinds: `request.created`, `request.allowed`, `request.pending`,
`request.approved`, `request.rejected`, `request.expired`, `request.denied`,
`request.completed`, `request.failed`, `token.created`, `token.revoked`,
`registry.reload`.

If anything is missing, that's a bug — file it.

## Where to learn more

- Architecture: [`design/01-architecture.md`](design/01-architecture.md)
- Principles: [`design/00-principles.md`](design/00-principles.md)
- Broker spec: [`design/10-broker.md`](design/10-broker.md)
- Toolyard spec: [`design/20-toolyard.md`](design/20-toolyard.md)
- Tool template: [`design/21-tool-template.md`](design/21-tool-template.md)
- Secrets: [`design/40-secrets.md`](design/40-secrets.md)
- ADRs: [`design/decisions/`](design/decisions/)
- End-to-end testing recipe: [`end-to-end-testing.md`](end-to-end-testing.md)


## Writable tool secrets

Tools that rotate their own credentials, such as OAuth refresh tokens, declare
specific fields as writable in `toolyard.yaml`:

```yaml
secrets:
  - name: refresh_token
    vault: ToolServer
    item: oauth-demo
    field: REFRESH_TOKEN
    writable: true
```

The container does not receive a 1Password token. It receives a per-tool Unix
socket mounted at `/run/toolyard/secrets.sock`. To update an allowlisted field:

```bash
curl --unix-socket /run/toolyard/secrets.sock   -X POST http://toolyard/v1/secrets/refresh_token   -H "Content-Type: application/json"   -d '{"value":"NEW_REFRESH_TOKEN","reason":"oauth refresh"}'
```

Toolyardd patches only the exact `(vault, item, field)` declared for that secret
name and writes a local audit event. Undeclared or read-only fields are denied.
