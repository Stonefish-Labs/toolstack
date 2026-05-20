# End-to-end Testing: Broker + Toolyardd + Discord Bot

This validates the REST path (`hello-rest`), MCP path (`time-mcp`), registry
reload, and writable-secret proxy behavior.

## Prerequisites

- Component venvs are installed.
- `/home/admin/.config/toolstack/*.env` files are filled in.
- 1Password vault `ToolServer` has item `hello-rest` with field `API_KEY`.
- Broker service is initialized with tokens for `svc.approver`, `svc.toolyard`,
  and `agent.codex`.
- `broker.service`, `toolyardd.service`, and `discord-approver.service` are
  running.

## 1. Service Health

```bash
systemctl status broker.service toolyardd.service discord-approver.service --no-pager
curl -s http://127.0.0.1:8765/v1/health
curl -s https://broker.your-tailnet.ts.net/v1/health
```

Expected health response: `{"ok": true}`.

## 2. Registry Includes First Tools

```bash
curl -s -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/agent-codex.token)"   http://127.0.0.1:8765/v1/registry | jq '.tools | keys'
```

Expected: `hello-rest` and `time-mcp`.

## 3. REST Tool Through Broker

```bash
curl -s -X POST http://127.0.0.1:8765/v1/actions/hello-rest.greet \
  -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/agent-codex.token)" \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"name":"agent"},"reason":"e2e rest"}' | jq
```

Expected: a `200` response containing `hello agent`.

## 4. MCP Tool Through Broker

```bash
curl -s -X POST http://127.0.0.1:8765/mcp/time-mcp \
  -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/agent-codex.token)" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' | jq

curl -s -X POST http://127.0.0.1:8765/mcp/time-mcp \
  -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/agent-codex.token)" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"current_time","arguments":{}},"id":2}' | jq
```

Expected: `tools/list` shows `current_time` and `time_in`; `tools/call` returns
an ISO timestamp.

## 5. Writable Secret Proxy

Use a test tool with a descriptor field like:

```yaml
secrets:
  - name: refresh_token
    field: REFRESH_TOKEN
    writable: true
```

From inside that container:

```bash
curl --unix-socket /run/toolyard/secrets.sock \
  -X POST http://toolyard/v1/secrets/refresh_token \
  -H "Content-Type: application/json" \
  -d '{"value":"NEW_REFRESH_TOKEN","reason":"oauth refresh"}'
```

Expected: `200 {"ok": true, ...}` and a `secret.update.completed` record in
`/home/admin/toolstack/toolyard/state/toolyard-audit.jsonl`.

Negative tests:

```bash
curl --unix-socket /run/toolyard/secrets.sock \
  -X POST http://toolyard/v1/secrets/client_id \
  -H "Content-Type: application/json" \
  -d '{"value":"SHOULD_NOT_WRITE"}'
```

Expected: `403` unless `client_id` is also declared `writable: true`.

Verify no Connect token is present in the container:

```bash
docker exec toolyard-<id> env | grep OP_CONNECT || true
docker exec toolyard-<id> sh -c 'find /run -name "*connect*" -o -name "*token*"'
```

Expected: no 1Password Connect token file or env var.

## 6. Approval Flow

Temporarily move `hello-rest.greet` from `allowed_ops` to `review_ops` in
`broker/policies/profiles/home-default.yaml`, then reload:

```bash
curl -X POST -H "Authorization: Bearer $(cat /home/admin/.config/toolstack/tokens/broker-registry-admin.token)"   http://127.0.0.1:8765/v1/registry/reload
```

Trigger the action again. Expected: broker returns `202 pending_review`, Discord
posts a card, approval completes the request, and broker audit shows the full
transition chain.

Revert the temporary policy change after the test.
