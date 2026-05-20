# Broker

The authority boundary. Authenticates agents, decides policy, orchestrates approval, forwards approved requests to tool servers, audits everything. The only thing the agent can address.

Target size: 500–800 LOC of Python.

## Responsibilities

The broker owns:

- **Authentication**: bearer tokens, hashed in SQLite, mapped to a `(caller, profile)`.
- **Policy decisions**: for each action, decide allow / review / deny based on the caller's profile.
- **Approval lifecycle**: create `pending_review` records, accept approve/reject from the Discord bot, time out to `expired`.
- **Forwarding**: HTTP for REST tools, blind JSON-RPC for MCP tools.
- **Audit**: every state transition recorded with caller, profile, tool, op, decision, actor, timing.
- **Registry**: reads `tools/<id>/toolyard.yaml` files to know which `tool_id` maps to which `127.0.0.1:port`.

The broker does NOT:

- Execute tool code.
- Resolve upstream API credentials (no 1Password Connect calls except for its own bot/operational secrets).
- Parse MCP protocol beyond extracting the method name and (for `tools/call`) the operation name.
- Host an HTML approval UI (the Discord bot is the approval surface).

## HTTP surface

All endpoints require `Authorization: Bearer <token>` unless noted. When `BROKER_APPROVER_SIGNING_SECRET_FILE` is configured, callers with the `approver` profile must also include valid HMAC signing headers.

### Action invocation

```
POST /v1/actions/<tool>.<op>
Body: { "arguments": {...}, "reason": "<optional human reason>" }
```

Responses:
- `200 OK { "result": {...} }` — allowed and executed
- `202 Accepted { "request_id": <id>, "status": "pending_review" }` — needs approval
- `403 Forbidden { "error": "denied", "reason": "..." }` — policy denied
- `404 Not Found { "error": "unknown_tool" | "unknown_op" }`
- `502 Bad Gateway { "error": "tool_unreachable" | "tool_failed", "detail": "..." }`

### MCP forwarding

```
POST /mcp/<tool>
Body: <JSON-RPC frame>
```

The broker:
1. Authenticates bearer.
2. Looks up `tool` in the registry.
3. For `method == "tools/call"`: peeks `params.name`, evaluates policy as if it were `/v1/actions/<tool>.<params.name>`. May return `202 pending_review` (in a JSON-RPC-compatible error shape) if approval needed.
4. For `tools/list`, `initialize`, etc.: auto-allowed if the profile allows `tool` at all.
5. Forwards the JSON-RPC frame as-is to `http://127.0.0.1:<port>/mcp` on the tool.
6. Returns the JSON-RPC response as-is.

See [ADR 002](decisions/002-blind-jsonrpc-routing.md).

### Approval management

```
GET  /v1/requests?status=<status>&limit=<n>&after_id=<id>
GET  /v1/requests/<id>
POST /v1/requests/<id>/approve   Body: { "approver": "<user>", "note": "<optional>" }
POST /v1/requests/<id>/reject    Body: { "approver": "<user>", "reason": "<required>" }
```

Approve/reject are restricted to callers whose profile allows it. The Discord bot uses the `approver` profile, which allows approval/list/audit operations but not `broker.registry.reload`; if approver signing is configured, these calls also require HMAC headers.

Approver HMAC headers are `X-Toolstack-Timestamp`, `X-Toolstack-Nonce`, and `X-Toolstack-Signature: v1=<hex-hmac-sha256>`. The signed base string is `METHOD\npath?query\ntimestamp\nnonce\nsha256(raw_body)`. Timestamps are accepted within a 5 minute skew, and nonces are cached in memory to reject replay.

### Audit

```
GET  /v1/audit?after_id=<id>&limit=<n>
GET  /v1/audit/<id>
```

### Operational

```
GET  /v1/registry              # tools known + addresses
POST /v1/registry/reload       # re-read toolyard.yaml/profile files; registry-admin only
GET  /v1/health                # liveness, unauthenticated, returns {"ok": true}
```

## Data model (SQLite)

```sql
callers (
  id              INTEGER PRIMARY KEY,
  name            TEXT UNIQUE NOT NULL,    -- e.g., "agent.hermes"
  profile         TEXT NOT NULL,           -- e.g., "home-default"
  created_at      INTEGER NOT NULL,
  revoked_at      INTEGER                  -- nullable
);

tokens (
  token_hash      TEXT PRIMARY KEY,        -- sha256 of raw bearer
  caller_id       INTEGER NOT NULL REFERENCES callers(id),
  created_at      INTEGER NOT NULL,
  last_used_at    INTEGER,
  revoked_at      INTEGER
);

action_requests (
  id                  INTEGER PRIMARY KEY,
  caller_id           INTEGER NOT NULL REFERENCES callers(id),
  tool                TEXT NOT NULL,
  op                  TEXT NOT NULL,
  args_json           TEXT NOT NULL,       -- secrets stripped before storage
  reason              TEXT,
  status              TEXT NOT NULL,       -- see status enum below
  policy_decision     TEXT NOT NULL,       -- JSON: effect, reason, ttl_seconds
  result_json         TEXT,                -- present after dispatch
  error               TEXT,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  expires_at          INTEGER               -- for pending_review
);

approvals (
  id              INTEGER PRIMARY KEY,
  request_id      INTEGER NOT NULL REFERENCES action_requests(id),
  approver        TEXT NOT NULL,
  action          TEXT NOT NULL,           -- "approve" | "reject"
  note            TEXT,
  created_at      INTEGER NOT NULL
);

grants (
  id              INTEGER PRIMARY KEY,
  caller_id       INTEGER NOT NULL REFERENCES callers(id),
  tool            TEXT NOT NULL,
  op              TEXT NOT NULL,
  scope_json      TEXT,                    -- e.g., arg fingerprint for prompt-once
  expires_at      INTEGER NOT NULL
);

audit_events (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL,
  request_id      INTEGER,
  caller_id       INTEGER,
  tool            TEXT,
  op              TEXT,
  detail_json     TEXT,
  created_at      INTEGER NOT NULL
);
```

Status enum for `action_requests`:

| Status | Meaning |
|---|---|
| `pending_review` | Policy required review. Awaiting human decision. |
| `approved` | Approved but not yet dispatched (transient). |
| `rejected` | Human rejected. Terminal. |
| `expired` | Timed out before approval. Terminal. Cannot be retroactively approved. |
| `denied` | Policy denied. Terminal. |
| `running` | Dispatched to tool; in flight. |
| `completed` | Tool returned success. Terminal. |
| `failed` | Tool returned an error or was unreachable. Terminal. |

Audit event `kind` values include: `token.created`, `token.revoked`, `request.created`, `request.allowed`, `request.pending`, `request.approved`, `request.rejected`, `request.expired`, `request.denied`, `request.failed`, `request.completed`, `registry.reload`.

## Policy interface

The decision function is the swappable seam. v1 reads YAML; future versions can swap to OPA, Cedar, or an agent evaluator without touching call sites.

```python
@dataclass
class PolicyInput:
    caller_id: int
    profile: str
    tool: str
    op: str
    arguments: dict
    reason: str | None
    active_grants: list[Grant]

@dataclass
class PolicyDecision:
    effect: str        # "allow" | "review" | "deny"
    reason: str
    grant_ttl_seconds: int | None   # if approved, how long to grant similar requests

def decide(input: PolicyInput) -> PolicyDecision: ...
```

For v1, `decide` reads `policies/profiles/<profile>.yaml`. See [ADR 005](decisions/005-policy-simple-now.md).

Example `policies/profiles/home-default.yaml`:

```yaml
profile: home-default
allowed_tools:
  - media
  - tasks
  - calendar
denied_tools:
  - admin
allowed_ops:
  - "media.get_*"          # read-only Media
  - "tasks.list_*"
review_ops:
  - "media.skip_*"         # require approval
  - "tasks.create_*"
denied_ops:
  - "*.delete_*"
risk_class_default:
  read: allow
  write: review
  destructive: deny
auto_grant_ttl_seconds: 3600
```

## Configuration

Env vars:

| Var | Default | Purpose |
|---|---|---|
| `BROKER_BIND_ADDR` | `127.0.0.1:8765` | HTTP listener |
| `BROKER_STATE_DIR` | `./state` | SQLite + runtime state |
| `BROKER_TOOLS_DIR` | `./tools` | where `<id>/toolyard.yaml` files live |
| `BROKER_POLICIES_DIR` | `./policies/profiles` | per-profile ACL YAMLs |
| `BROKER_APPROVAL_TIMEOUT_SECONDS` | `86400` | pending → expired |
| `BROKER_GRANT_DEFAULT_TTL_SECONDS` | `3600` | default grant duration |
| `BROKER_PUBLIC_URL` | (unset) | base URL for outbound links (Discord bot needs this) |
| `BROKER_APPROVER_SIGNING_SECRET_FILE` | (unset) | file containing HMAC secret required for `approver` profile calls when set |

## Operational CLI

`brokerctl`:

```
brokerctl init-db
brokerctl create-caller --name <name> --profile <profile>     # prints raw token once
brokerctl list-tokens [--include-revoked] [--json]
brokerctl revoke-token <hash-prefix-or-raw-token>
brokerctl list-requests [--status <status>] [--limit <n>] [--json]
brokerctl audit [--after-id <id>] [--limit <n>] [--json]
brokerctl reload-registry                                        # local CLI reload
brokerctl serve [--bind 127.0.0.1:8765]
```

## Fail-closed semantics

Every path defaults to deny:

- Unknown bearer → 401, audit `request.denied`.
- Missing, stale, replayed, or invalid approver HMAC signature → 401 before approval/list/audit handling.
- Caller revoked → 401.
- Profile not found in `BROKER_POLICIES_DIR` → 500 + audit. The broker refuses to start if it cannot load the policies it references.
- Tool not in registry → 404.
- Policy decision function raises → deny + audit (do not "fail open").
- Approval pending past `BROKER_APPROVAL_TIMEOUT_SECONDS` → `expired`, cannot be approved.
- Tool container unreachable → 502, request marked `failed`, audit.
- Discord bot down → approvals remain `pending_review` until they expire. Broker is unaffected.

## Argument redaction

Before storing `arguments` in `action_requests.args_json` or audit details:

- Strip any fields named like `password`, `token`, `secret`, `api_key`, `authorization` (configurable regex list).
- For known tool ops with sensitive args (declared in the toolyard.yaml or a separate redaction map), apply the per-op redaction.
- Never store a value that the tool received from 1Password Connect. The broker should never have seen it in the first place; this is a defense-in-depth check.

## Forwarding mechanics

REST forwarding:

```
POST /v1/actions/<tool>.<op>     ───►   POST http://127.0.0.1:<port>/v1/actions/<op>
Body: {arguments, reason}                Body: {arguments, reason, broker_request_id, caller}
```

The broker adds:
- `broker_request_id` — the audit trail key
- `caller` — `{name, profile}` for the tool to optionally log

The tool returns:
```
200 OK { "result": <any> }
```
or
```
4xx/5xx { "error": "<code>", "detail": "<human>" }
```

MCP forwarding:

```
POST /mcp/<tool>     ───►   POST http://127.0.0.1:<port>/mcp
Body: <JSON-RPC frame, unmodified>
```

JSON-RPC response is returned unmodified to the agent, except errors caught by the broker (policy denial, registry miss) come back as JSON-RPC error responses with the broker's error codes.

## What's deliberately small

The broker is small on purpose:

- No background workers beyond a single approval-timeout reaper.
- No long-lived connections to tools (HTTP requests are short-lived).
- No client SDK; the agent uses plain HTTP.
- No metrics stack; SQLite audit is the observability surface for v1.
- No retry logic on tool failures (tools own their own retries internally if needed).

If the broker grows past ~1,000 LOC, the next concern needs its own service.
