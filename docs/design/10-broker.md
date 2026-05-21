# Broker

The broker is the authority boundary. Agents and service bots hold broker
bearer tokens, request semantic actions, and the broker decides whether each
operation is allowed, sent to review, or denied.

## Responsibilities

- Authenticate bearer tokens and map each token to one concrete caller.
- Store the caller's policy directly in SQLite.
- Evaluate policy for `tool.operation` requests and broker control operations.
- Create approval records for review-required actions and accept Discord
  approve/reject decisions.
- Forward approved calls to REST or MCP HTTP tools.
- Audit token, policy, request, approval, dispatch, and registry events.
- Read `toolyard.yaml` files from the configured tools root to know tool ports,
  risks, and operation descriptions.

The broker does not execute tool code and does not resolve downstream tool
secrets. Toolyard resolves Infisical-backed workload secrets before containers
start.

## Caller Policy

Profiles are not part of the runtime model. A caller is the identity, and its
policy is the capability bundle.

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

Missing tools and operations deny by default. `deny` values may be present in
admin API payloads, but the stored policy only needs to retain enabled
`allow`/`review` operations because missing still means deny.

`broker_ops` authorizes broker control endpoints such as:

- `broker.approve`
- `broker.reject`
- `broker.list_requests`
- `broker.audit`
- `broker.registry.reload`
- `broker.admin.*`
- `broker.approval_messages.read`
- `broker.approval_messages.write`

The admin API also exposes registry reload for Broker Panel under
`broker.admin.tools.write`. This is the operator-facing equivalent of the
narrow `broker.registry.reload` service hook used by Toolyard.
Broker Panel also reaches Toolyard lifecycle controls through broker admin
endpoints, so start/stop/restart actions are authorized and audited by the
broker before crossing the local Toolyard control socket.

## HTTP Surface

All endpoints require `Authorization: Bearer <token>` except `GET /v1/health`.
When `BROKER_APPROVER_SIGNING_SECRET_FILE` is configured, any caller policy that
can approve or reject must also include valid HMAC signing headers for requests.

### Actions

```text
POST /v1/actions/<tool>.<op>
Body: {"arguments": {...}, "reason": "<optional>"}
```

Responses:

- `200` allowed and executed.
- `202` pending human review.
- `403` denied by policy.
- `404` unknown tool or operation.
- `502` tool unreachable or failed.

### MCP

```text
POST /mcp/<tool>
Body: <JSON-RPC frame>
```

For `tools/call`, the broker extracts `params.name` and evaluates the same
caller policy path as REST actions. For `tools/list`, `initialize`, and other
non-call methods, the broker forwards only when the caller policy enables at
least one operation for that tool.

### Approval

```text
GET  /v1/requests?status=<status>&limit=<n>&after_id=<id>
GET  /v1/requests/<id>
POST /v1/requests/<id>/approve
POST /v1/requests/<id>/reject
```

The Discord approver service normally owns these broker ops. It may also use
approval-message endpoints to remember which Discord message belongs to a
request.

### Admin

```text
GET    /v1/admin/tools
GET    /v1/admin/callers
POST   /v1/admin/callers
GET    /v1/admin/callers/<name>/policy
PUT    /v1/admin/callers/<name>/policy
POST   /v1/admin/callers/<name>/refresh-token
DELETE /v1/admin/callers/<name>
GET    /v1/admin/tokens
DELETE /v1/admin/tokens/<hash-prefix>
```

`/v1/admin/tools` includes operation descriptions from `toolyard.yaml` so the
panel can explain what each toggle enables.

## SQLite Shape

```sql
callers (
  id          INTEGER PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,
  created_at  INTEGER NOT NULL,
  revoked_at  INTEGER
);

caller_policies (
  caller_id              INTEGER PRIMARY KEY REFERENCES callers(id),
  policy_json            TEXT NOT NULL,
  auto_grant_ttl_seconds INTEGER,
  created_at             INTEGER NOT NULL,
  updated_at             INTEGER NOT NULL
);

tokens (
  token_hash   TEXT PRIMARY KEY,
  caller_id    INTEGER NOT NULL REFERENCES callers(id),
  created_at   INTEGER NOT NULL,
  last_used_at INTEGER,
  revoked_at   INTEGER
);
```

`action_requests`, `approvals`, `approval_messages`, `grants`, and
`audit_events` keep the existing request lifecycle and audit trail. Action
request API responses include `caller`, `tool`, `op`, `arguments`, `reason`,
`status`, `risk`, `expires_at`, `approver`, and `decision_note`.

## CLI

```text
brokerctl init-db
brokerctl create-caller --name <name> [--allow TOOL.OP] [--review TOOL.OP] [--broker-op broker.OP] [--ttl seconds]
brokerctl refresh-token <caller-name>
brokerctl list-callers [--json] [--include-revoked]
brokerctl revoke-caller <name>
brokerctl list-tokens [--json] [--include-revoked]
brokerctl revoke-token <hash-prefix>
brokerctl list-requests [--status <status>] [--limit <n>] [--json]
brokerctl audit [--after-id <id>] [--limit <n>] [--json]
brokerctl reload-registry
brokerctl serve
```

The panel is the preferred way to edit caller policy because it shows tool and
operation descriptions while changing permissions.

## Forwarding

REST forwarding:

```text
POST /v1/actions/<tool>.<op> -> POST http://127.0.0.1:<port>/v1/actions/<op>
```

The broker adds `broker_request_id` and `caller: {"name": "<caller>"}` to the
tool request body. MCP frames are forwarded unchanged after policy checks.
