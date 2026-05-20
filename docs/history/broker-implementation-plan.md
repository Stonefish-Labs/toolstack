# Historical Implementation Plan: Broker

This document is archived implementation history. It describes an earlier broker
build slice and is not the current project status or source of truth. For
current behavior, see the component README and the active design docs under
`../design/`.

**Audience**: this is a hand-off plan for an implementer (likely another Claude model) who has not seen the prior conversation. Read the referenced design docs first — they have the architectural context.

## What you're building

The broker is the authority boundary of the toolserver system: agents authenticate to the broker, request semantic actions, and the broker decides allow / require review / deny based on profile-driven policy. For review-required actions, the broker holds the request in `pending_review` while a separately-deployed Discord approver bot collects a human decision. The broker then either dispatches the action to a tool server or returns the rejection.

**This slice builds everything except real tool dispatch.** Dispatch is *synthetic*: the broker pretends to call the tool and returns a stub result. The HTTP forwarding / MCP JSON-RPC plumbing comes in a later slice, once the toolyard and a real tool exist.

What this slice gives you when complete:
- A real broker the existing Discord approver bot can talk to (replacing its `fake_broker.py`).
- End-to-end token issuance → policy decision → approval lifecycle → audit, all persisted in SQLite.
- A working `brokerctl` CLI for operator tasks.
- Synthetic dispatch wired through a `Dispatcher` protocol so the real HTTP/MCP dispatchers plug in cleanly later.

## Required reading

Before writing code, read these in order:

1. `../trust-agents-with-action-not-access.md` — the system's thesis (skim).
2. `../design/00-principles.md` — operational principles.
3. `../design/01-architecture.md` — where the broker fits in the four-component shape.
4. `../design/10-broker.md` — **the spec for this component.** Read fully. Note the HTTP surface, SQLite schema, policy interface, fail-closed semantics, and forwarding mechanics (you'll implement the auth/policy/lifecycle/audit parts; the forwarding parts get stubbed via synthetic dispatch).
5. `../design/decisions/001-token-granularity.md` — tokens are (caller, profile) bound.
6. `../design/decisions/005-policy-simple-now.md` — simple YAML ACL now; the decision function is a swap seam.
7. `../design/30-approver-discord.md` — for context on what the bot expects from the broker. Your API has to match the contract the bot was built against.
8. `discord-approver-implementation-plan.md` — for the same reason. Pay attention to the `BrokerClient` interface in that plan; your HTTP API needs to satisfy it.

If anything in this plan conflicts with the design docs, the design docs win — flag the conflict and ask before deviating.

## Goal

A working broker that:

1. Issues bearer tokens for callers (`brokerctl create-caller`) and verifies them on every authenticated request.
2. Evaluates a profile-driven YAML ACL to decide allow / review / deny per action request.
3. Persists every request, approval, grant, and audit event in SQLite.
4. Exposes the HTTP API in [`../design/10-broker.md`](../design/10-broker.md), matching the contract the Discord bot was built against.
5. Synthetically dispatches allowed actions (no real tool calls — returns a stub result).
6. Times out pending approvals to `expired` and fails closed on unknown states.
7. Provides a `brokerctl` CLI for operators.
8. Can be paired with the already-built Discord approver bot end-to-end (replacing the bot's `fake_broker.py`).

## Loose-coupling requirements

To keep this broker swappable and the architecture honest:

1. **`Dispatcher` protocol** — the broker calls `dispatcher.dispatch(request)` and gets back a `DispatchResult`. Implementations: `SyntheticDispatcher` (this slice) and later `HTTPDispatcher` + `MCPDispatcher`. The broker must not import any dispatch implementation directly — only the protocol.
2. **Pluggable policy decision function** — `decide(input: PolicyInput) -> PolicyDecision` is the swap seam. v1 implementation reads YAML. Per [ADR 005](../design/decisions/005-policy-simple-now.md), the broker should be able to swap to OPA, Cedar, or an agentic evaluator later without touching call sites.
3. **Registry as a separate module** — `registry.py` knows how to read `tools/<id>/toolyard.yaml` files. Other modules never read those files directly. This decouples the broker from the toolyard's eventual on-disk layout.
4. **Audit recording is a sink, not a side effect of every function** — pass an `Auditor` (or equivalent) to handlers; they call `auditor.record(...)`. This makes it easy to swap to JSONL export or off-host replication later.

The test for these seams: `policy.py`, `lifecycle.py`, `approval.py` should be testable without FastAPI, without HTTP, and without a real Dispatcher — just with mocks.

## Project layout

```
broker/
├── README.md                         # write this as part of step 1
├── pyproject.toml                    # or requirements.txt — pick one
├── .gitignore                        # standard Python ignores + state/ + *.sqlite3
├── src/broker/
│   ├── __init__.py
│   ├── config.py                     # env var loading + validation
│   ├── models.py                     # dataclasses / Pydantic models for domain types
│   ├── db.py                         # SQLite schema + CRUD primitives
│   ├── tokens.py                     # bearer token creation, hashing, verification
│   ├── policy.py                     # YAML ACL loader + decide() function
│   ├── registry.py                   # reads tools/<id>/toolyard.yaml (stubbed for this slice)
│   ├── dispatch.py                   # Dispatcher protocol + SyntheticDispatcher
│   ├── lifecycle.py                  # request state transitions
│   ├── approval.py                   # approve/reject logic + grant creation
│   ├── audit.py                      # audit event recording
│   ├── timeouts.py                   # background reaper: pending_review → expired
│   ├── api.py                        # FastAPI app + route handlers
│   └── cli.py                        # brokerctl entry point
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # pytest fixtures: temp DB, sample profiles, etc.
│   ├── test_tokens.py
│   ├── test_db.py
│   ├── test_policy.py
│   ├── test_registry.py
│   ├── test_lifecycle.py
│   ├── test_approval.py
│   ├── test_timeouts.py
│   ├── test_api.py                   # FastAPI TestClient
│   └── test_cli.py
├── policies/
│   └── profiles/
│       ├── home-default.yaml         # example agent profile
│       ├── readonly.yaml             # example read-only profile
│       └── approver.yaml             # for the Discord bot
└── docs/
    └── manual-testing.md             # write as part of step N — end-to-end with bot
```

State directory (created at runtime, not in the repo):

```
state/
├── broker.sqlite3                    # persistent state
└── (other transient files if any)
```

## Module-by-module spec

### `config.py`

Load env vars, fail fast on anything required missing.

| Env var | Required | Default | Notes |
|---|---|---|---|
| `BROKER_BIND_ADDR` | no | `127.0.0.1:8765` | HTTP listener |
| `BROKER_STATE_DIR` | no | `./state` | Where `broker.sqlite3` lives |
| `BROKER_TOOLS_DIR` | no | `./tools` | Where `<id>/toolyard.yaml` files live (may be empty for this slice) |
| `BROKER_POLICIES_DIR` | no | `./policies/profiles` | Per-profile YAML files |
| `BROKER_APPROVAL_TIMEOUT_SECONDS` | no | `86400` | Pending → expired |
| `BROKER_GRANT_DEFAULT_TTL_SECONDS` | no | `3600` | Default grant duration |
| `BROKER_ALLOW_UNKNOWN_TOOLS` | no | `false` | If `true`, accept any tool name (dev shortcut). If `false`, tool must be in `BROKER_TOOLS_DIR` |
| `BROKER_PUBLIC_URL` | no | unset | Used in pending-request response bodies if set (for the bot's link generation, optional) |

### `models.py`

Dataclasses or Pydantic models. Pick one and stay consistent.

```python
class RequestStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"          # transient; resolves to running
    REJECTED = "rejected"
    EXPIRED = "expired"
    DENIED = "denied"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class Caller:
    id: int
    name: str               # e.g. "agent.hermes"
    profile: str            # e.g. "home-default"
    created_at: int
    revoked_at: int | None

class ActionRequest:
    id: int
    caller_id: int
    caller_name: str        # denormalized for convenience
    profile: str            # denormalized
    tool: str
    op: str
    arguments: dict
    reason: str | None
    status: RequestStatus
    policy_decision: dict   # {"effect": ..., "reason": ..., "ttl_seconds": ...}
    result: dict | None
    error: str | None
    approver: str | None
    decision_note: str | None
    created_at: int
    updated_at: int
    expires_at: int | None
    risk: str               # "read" | "write" | "destructive" — from policy decision

class PolicyInput:
    caller_id: int
    profile: str
    tool: str
    op: str
    arguments: dict
    reason: str | None
    active_grants: list[Grant]

class PolicyDecision:
    effect: Literal["allow", "review", "deny"]
    reason: str
    risk: str               # "read" | "write" | "destructive"
    grant_ttl_seconds: int | None

class Grant:
    id: int
    caller_id: int
    tool: str
    op: str
    expires_at: int

class DispatchResult:
    success: bool
    result: dict | None
    error: str | None
```

### `db.py`

SQLite schema **must match** [`../design/10-broker.md`](../design/10-broker.md) "Data model (SQLite)" section — schema is in that doc verbatim. Add `created_at` indexes where useful for the timeout reaper and listing.

CRUD primitives:
- `init_db(db_path)` — create tables if absent.
- `callers`: `create_caller`, `get_caller_by_id`, `get_caller_by_name`, `revoke_caller`, `list_callers`.
- `tokens`: `create_token(caller_id, token_hash)`, `get_token(token_hash)`, `revoke_token(prefix)`, `list_tokens(include_revoked=False)`, `update_last_used(token_hash)`.
- `action_requests`: `create_request(...)`, `get_request(id)`, `update_request_status(...)`, `list_requests(status, limit, after_id)`, `find_expired_pending(now)`.
- `approvals`: `record_approval(request_id, approver, action, note)`, `list_for_request(request_id)`.
- `grants`: `create_grant(caller_id, tool, op, expires_at)`, `find_active_grant(caller_id, tool, op, now)`, `purge_expired(now)`.
- `audit_events`: `record(kind, request_id, caller_id, tool, op, detail)`, `list(after_id, limit)`.

Use stdlib `sqlite3`. Single-file DB. Set `journal_mode=WAL` for concurrent readers + reaper.

Test against a temp DB with `tmp_path` fixture.

### `tokens.py`

```python
def generate_raw_token() -> str:
    """Return a cryptographically-random URL-safe token (32 bytes -> base64)."""

def hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token."""

def create_token_for_caller(caller_id: int, db) -> tuple[str, str]:
    """Generate, hash, store. Return (raw_token, hash_prefix). Raw is shown to operator once."""

def verify_bearer(authorization_header: str, db) -> Caller | None:
    """Strip 'Bearer ', hash, look up. Return Caller if valid and not revoked. Update last_used_at on hit."""
```

Use `secrets.token_urlsafe(32)` for generation. SHA-256 the raw bearer before lookup. Constant-time comparison not strictly necessary (we look up by hash, not compare) but don't be sloppy.

### `policy.py`

Per-profile YAML schema (the design doc has the canonical example):

```yaml
profile: home-default
allowed_tools:
  - media
  - tasks
denied_tools:
  - admin
allowed_ops:
  - "media.get_*"
review_ops:
  - "media.skip_*"
  - "tasks.create_*"
denied_ops:
  - "*.delete_*"
risk_class_default:
  read: allow
  write: review
  destructive: deny
auto_grant_ttl_seconds: 3600
```

Plus a special approver profile:

```yaml
profile: approver
# Special "broker.*" ops grant access to approval endpoints, not tool dispatch.
allowed_ops:
  - "broker.approve"
  - "broker.reject"
  - "broker.list_requests"
  - "broker.audit"
```

Decision function:

```python
def decide(input: PolicyInput) -> PolicyDecision:
    # 1. Load profile YAML from BROKER_POLICIES_DIR/profiles/<profile>.yaml.
    #    If not found, deny.
    # 2. Check active_grants for (caller, tool, op). If a non-expired grant exists, return allow with no new grant.
    # 3. Apply rules in order:
    #    a. denied_tools matches?           -> deny
    #    b. denied_ops glob matches?        -> deny
    #    c. allowed_ops glob matches?       -> allow
    #    d. review_ops glob matches?        -> review (with auto_grant_ttl_seconds)
    #    e. allowed_tools matches AND no specific op rule? -> apply risk_class_default for the inferred risk
    #    f. nothing matches?                -> deny (fail closed)
```

Risk inference for v1: read from the registry (if the tool has `operations[*].risk` declared in its `toolyard.yaml`, use that). Otherwise default to `"write"` (conservative).

Glob matching: `fnmatch` from stdlib (supports `*` and `?`).

### `registry.py`

For this slice, the registry is a simple reader of `BROKER_TOOLS_DIR/<id>/toolyard.yaml` files.

```python
def load_registry(tools_dir: Path) -> dict[str, ToolDescriptor]:
    """Walk BROKER_TOOLS_DIR, parse each toolyard.yaml, return id -> descriptor map."""

def get_tool(id: str) -> ToolDescriptor | None: ...

def reload(): ...
```

`ToolDescriptor` fields needed for this slice:
- `id`
- `type` (`rest` | `mcp-http` | `mcp-stdio`)
- `entrypoint.port`
- `operations` (list of `{op, risk}` — used by policy.py for risk inference)
- `risk_class_default`

The registry is **not** wired to forwarding yet (synthetic dispatch ignores it). But it's wired to policy (risk lookup) and to the endpoint that authorizes which tool names are accepted.

If `BROKER_TOOLS_DIR` is empty (likely for early development), the registry is empty. Combined with `BROKER_ALLOW_UNKNOWN_TOOLS=true`, you can hit `/v1/actions/anything.do_stuff` and the policy will use defaults.

### `dispatch.py`

```python
class Dispatcher(Protocol):
    async def dispatch(self, request: ActionRequest, descriptor: ToolDescriptor | None) -> DispatchResult: ...

class SyntheticDispatcher(Dispatcher):
    async def dispatch(self, request, descriptor) -> DispatchResult:
        # Default: always succeed with a stub.
        return DispatchResult(
            success=True,
            result={"synthetic": True, "tool": request.tool, "op": request.op,
                    "arguments_echo": request.arguments},
            error=None,
        )
```

Add a debug override: if `request.arguments.get("__synthetic_outcome") == "fail"`, return `DispatchResult(success=False, error="synthetic failure")`. This lets the bot exercise the `failed` status path during manual testing.

Future dispatchers (not in this slice): `HTTPDispatcher` for `type: rest`, `MCPDispatcher` for `type: mcp-*`. Same protocol.

### `lifecycle.py`

The state machine. Pure functions that take the current state and inputs, return the new state and audit events to record.

Main entry point:

```python
async def handle_action_request(
    *,
    caller: Caller,
    tool: str,
    op: str,
    arguments: dict,
    reason: str | None,
    db,
    dispatcher: Dispatcher,
    registry,
    auditor,
) -> tuple[ActionRequest, dict | None]:
    """
    Returns (request_record, immediate_result_or_None).
    If allowed: dispatches synchronously, returns (request, result).
    If review-required: creates pending record, returns (request, None).
    If denied: marks denied, returns (request, None).
    """
```

Internal flow:
1. Look up tool in registry. If not found and `BROKER_ALLOW_UNKNOWN_TOOLS` is false, return denied with reason "unknown_tool".
2. Load active grants for (caller, tool, op).
3. Call `policy.decide(...)`.
4. Insert `action_requests` row with `policy_decision` and computed `risk`.
5. Switch on decision.effect:
   - `allow`: status = `running`. Call `dispatcher.dispatch(...)`. On success: status = `completed`, store result. On failure: status = `failed`, store error.
   - `review`: status = `pending_review`, set `expires_at = now + BROKER_APPROVAL_TIMEOUT_SECONDS`. Don't dispatch.
   - `deny`: status = `denied`. Don't dispatch.
6. Record audit events for each transition.
7. Return.

### `approval.py`

```python
async def approve_request(
    *,
    request_id: int,
    approver: str,
    note: str | None,
    db,
    dispatcher: Dispatcher,
    registry,
    auditor,
) -> ActionRequest:
    """
    Idempotent? No — second call returns the current state without retrying dispatch.

    Flow:
    1. Load request. If not pending_review, return current state (no-op).
    2. Insert approval row.
    3. status = approved (transient).
    4. If decision.ttl_seconds > 0: create grant for (caller, tool, op).
    5. Record audit.
    6. Dispatch synchronously (same as the immediate-allow path in lifecycle.py).
    7. status = completed/failed based on dispatch.
    8. Return updated request.
    """

async def reject_request(
    *,
    request_id: int,
    approver: str,
    reason: str | None,
    db,
    auditor,
) -> ActionRequest:
    """
    1. Load request. If not pending_review, no-op.
    2. Insert approval row (action=reject).
    3. status = rejected, store decision_note = reason.
    4. Record audit.
    5. Return updated request.
    """
```

Both must check that the calling caller's profile has the right `broker.*` op authorization. That check lives at the HTTP layer (`api.py`), not in these functions — keep these pure.

### `audit.py`

```python
def record(kind: str, *, request_id=None, caller_id=None, tool=None, op=None, detail: dict | None = None, db):
    """Insert into audit_events. Strip secret-shaped fields from detail before storing."""
```

Standard `kind` values (suggested — pick a consistent set):
- `token.created`, `token.revoked`
- `request.created`, `request.allowed`, `request.pending`, `request.denied`
- `request.approved`, `request.rejected`, `request.expired`
- `request.completed`, `request.failed`
- `registry.reload`

Argument redaction: before storing `arguments` anywhere (action_requests row or audit detail), apply a configurable regex list to strip fields like `password`, `token`, `secret`, `api_key`, `authorization`. Default redaction list lives in `audit.py`; broker config can override.

### `timeouts.py`

A background task that runs every N seconds (default 30s):

```python
async def expire_pending_requests(db, auditor, now=None):
    """
    Find requests where status='pending_review' AND expires_at < now.
    For each: set status='expired', record audit event, no dispatch.
    """
```

Mount as a FastAPI startup background task or a separate asyncio task in `cli.py serve`.

Tests should drive the function directly with a controllable clock.

### `api.py`

FastAPI app. Endpoints per [`../design/10-broker.md`](../design/10-broker.md):

**Unauthenticated:**
- `GET /v1/health` → `{"ok": true}`

**Authenticated (bearer token required):**

- `POST /v1/actions/<tool>.<op>`
  - Body: `{"arguments": {...}, "reason": "<optional>"}`
  - Responses:
    - `200 OK {"result": ...}` — allowed and (synthetically) dispatched
    - `202 Accepted {"request_id": <id>, "status": "pending_review"}` — review required
    - `403 Forbidden {"error": "denied", "reason": "..."}` — policy denied
    - `404 Not Found {"error": "unknown_tool"}` — tool not in registry and `BROKER_ALLOW_UNKNOWN_TOOLS` is false
    - `401 Unauthorized` — invalid bearer

- `GET /v1/requests?status=<status>&limit=<n>&after_id=<id>`
  - Requires `broker.list_requests` in profile's `allowed_ops`.
  - Returns: `{"requests": [<ActionRequest>, ...]}`.

- `GET /v1/requests/<id>`
  - Requires `broker.list_requests`, OR caller is the original requester.
  - Returns: `<ActionRequest>`.

- `POST /v1/requests/<id>/approve`
  - Requires `broker.approve` in profile's `allowed_ops`.
  - Body: `{"approver": "<user>", "note": "<optional>"}`.
  - Calls `approval.approve_request(...)`.
  - Returns the updated `ActionRequest`.

- `POST /v1/requests/<id>/reject`
  - Requires `broker.reject` in profile's `allowed_ops`.
  - Body: `{"approver": "<user>", "reason": "<optional>"}`.
  - Calls `approval.reject_request(...)`.
  - Returns the updated `ActionRequest`.

- `GET /v1/audit?after_id=<id>&limit=<n>`
  - Requires `broker.audit` in profile's `allowed_ops`.
  - Returns: `{"events": [<AuditEvent>, ...]}`.

**Response shape consistency**: action requests serialized as the `ActionRequest` dataclass (`models.py`). The Discord bot expects fields `id, caller (string), profile, tool, op, arguments, reason, status, risk, expires_at, approver, decision_note`. Match those names exactly.

Bearer auth: use a FastAPI dependency that verifies the bearer and yields the `Caller`. Routes that need broker.* permissions check the profile's `allowed_ops` inside the handler.

### `cli.py`

`brokerctl` subcommands:

```
brokerctl init-db                                                # create schema in BROKER_STATE_DIR/broker.sqlite3
brokerctl create-caller --name <name> --profile <profile>       # prints raw token ONCE
brokerctl list-callers [--json]
brokerctl revoke-caller <name>
brokerctl list-tokens [--include-revoked] [--json]
brokerctl revoke-token <hash-prefix-or-raw-token>
brokerctl list-requests [--status <status>] [--limit <n>] [--json]
brokerctl approve <request-id> --approver <name> [--note <text>]
brokerctl reject <request-id> --approver <name> [--reason <text>]
brokerctl audit [--after-id <id>] [--limit <n>] [--json]
brokerctl reload-registry                                        # local reload (not over HTTP)
brokerctl serve [--bind 127.0.0.1:8765]                          # runs uvicorn
```

Use `argparse` (stdlib) or `click`. Either is fine; pick one and stay consistent.

Important: `serve` should run `uvicorn` under the hood (or use `uvicorn.run` from Python) and start the timeout reaper as a background task on FastAPI startup.

`create-caller` prints the raw token to stdout exactly once, with a clear "save this — it will not be shown again" notice. Logs (if any) MUST NOT contain raw tokens.

## Implementation order

Vertical slices, each independently testable:

1. **Project setup** — folder, `pyproject.toml`, README, `.gitignore`. Pick FastAPI + Pydantic + Uvicorn + pytest + pytest-asyncio. Verify imports work.
2. **`models.py`** — dataclasses or Pydantic models. No logic yet.
3. **`db.py` + `test_db.py`** — schema, CRUD primitives. Test against `tmp_path` fixture.
4. **`tokens.py` + `test_tokens.py`** — generation, hashing, verification. Test the round-trip.
5. **`audit.py` + `test_audit.py`** — recording + redaction. Test that secret-shaped fields are stripped.
6. **`policy.py` + `test_policy.py`** — YAML loading + `decide()`. Test each rule branch (allow / deny / review / risk-default / unknown profile = deny).
7. **`registry.py` + `test_registry.py`** — stub-load directory of `toolyard.yaml`. Test empty directory + a couple of fake descriptors.
8. **`dispatch.py` + `test_dispatch.py`** — `SyntheticDispatcher`. Test default success + `__synthetic_outcome=fail` override.
9. **`lifecycle.py` + `test_lifecycle.py`** — `handle_action_request`. Mock dispatcher + temp DB. Test each branch (allow → completed, review → pending, deny → denied, allow → failed via synthetic fail).
10. **`approval.py` + `test_approval.py`** — approve / reject with grant creation. Test idempotency: second approve is a no-op.
11. **`timeouts.py` + `test_timeouts.py`** — expire pending requests. Test with frozen time.
12. **`api.py` + `test_api.py`** — FastAPI routes via `TestClient`. Test auth (401 / 403), each route, response shapes. The end-to-end happy path is the headline test.
13. **`cli.py` + `test_cli.py`** — exercise each subcommand against a temp state dir.
14. **Manual testing doc + bot integration** — write `docs/manual-testing.md`, run through it.

Stop and ask if any seam pressure suggests deviating from the design docs.

## Testing

- pytest, pytest-asyncio.
- Every module gets a `test_*.py` file.
- Use `tmp_path` for SQLite temp files; tear down between tests.
- For `api.py`, use FastAPI's `TestClient` (or `httpx.AsyncClient` with `ASGITransport`).
- Don't mock SQLite — use a real temp DB. The whole point is that the DB schema and queries are tested.
- DO mock the `Dispatcher` in `test_lifecycle.py` and `test_approval.py` so you can assert exact dispatch calls.
- Reach for property-based tests (hypothesis) only if you want — not required for v1.

Target coverage: every public function in `db.py`, `tokens.py`, `policy.py`, `lifecycle.py`, `approval.py`, `audit.py`, `timeouts.py` has at least one test. `api.py` tests cover each route and each significant response branch.

## Manual testing procedure

Write `docs/manual-testing.md` covering:

1. **Setup**:
   - Install dependencies.
   - `brokerctl init-db`.
   - Configure `BROKER_POLICIES_DIR=./policies/profiles`.
   - Verify the three example profiles load (`home-default`, `readonly`, `approver`).

2. **Token creation**:
   - `brokerctl create-caller --name agent.hermes --profile home-default` → captures raw token.
   - `brokerctl create-caller --name bot.approver --profile approver` → captures raw token.
   - `brokerctl list-callers` → both visible.

3. **Authenticated request smoke test (curl)**:
   - `brokerctl serve` in a separate terminal.
   - `curl -H "Authorization: Bearer $AGENT_TOKEN" http://127.0.0.1:8765/v1/actions/media.get_playback_state -d '{"arguments": {}, "reason": "smoke test"}'`
   - Expect 200 with synthetic result (if policy allows) OR 202 pending_review (if policy says review) OR 403 (if denied).
   - Repeat with `__synthetic_outcome=fail` argument to verify the failure path.

4. **Approval flow via CLI**:
   - Trigger a review-required action (e.g., `media.skip_track`).
   - `brokerctl list-requests --status pending_review` → see the pending row.
   - `brokerctl approve <id> --approver test --note "manual approval"` → status transitions to completed (with synthetic result).
   - `brokerctl audit --limit 20` → see all the events.

5. **Timeout flow**:
   - Set `BROKER_APPROVAL_TIMEOUT_SECONDS=10` and restart broker.
   - Trigger a review-required action; wait 15 seconds.
   - `brokerctl list-requests --status expired` → the request should be there.
   - Try to approve it → expect 4xx (cannot approve expired).

6. **Token revocation**:
   - `brokerctl revoke-token <hash-prefix>`.
   - Curl with the revoked token → 401.

7. **Bot integration** (the headline test):
   - Spin up the Discord approver bot from `../../discord-approver/`.
   - Configure the bot's `APPROVER_BROKER_URL` to point at this broker.
   - Configure `APPROVER_BROKER_TOKEN_FILE` with the `bot.approver` raw token.
   - Stop using `scaffolding/fake_broker.py` entirely.
   - From a curl-as-the-agent, trigger a review-required action.
   - Verify the bot posts a card in Discord.
   - Click Approve → broker transitions to completed → bot edits the message.
   - Repeat for Reject+Reason; verify the reason is stored in `decision_note` (and the broker returns it to the agent).
   - Force-expire via short timeout → bot edits the message to "Timed out".

This is the slice's win condition: the same bot that worked against the fake broker now works against the real one with zero code changes.

## Acceptance criteria

- [ ] All unit tests pass (`pytest tests/`).
- [ ] Manual test procedure passes end-to-end, including the bot-integration step.
- [ ] The Discord bot's `HTTPBrokerClient` works against this broker without modification (matches the API contract from `../design/10-broker.md`).
- [ ] `policy.py`, `lifecycle.py`, `approval.py`, `timeouts.py` are testable without FastAPI imports (verify by inspection — these modules shouldn't import `fastapi`).
- [ ] `SyntheticDispatcher` is the only `Dispatcher` implementation in `dispatch.py`; the protocol is clearly defined so HTTP/MCP dispatchers slot in later.
- [ ] No secret-shaped fields appear in audit `detail_json` or `arguments_json` (verify by writing a test that records an audit event with `arguments={"password": "x"}` and asserting it's redacted).
- [ ] Expired pending requests cannot be retroactively approved (test + manual).
- [ ] Token revocation takes effect on the next request (no caching of validity).
- [ ] Raw bearer tokens never appear in logs (run `serve` with a created token and search the captured logs for the raw string — must not be present).

## Out of scope (do NOT build)

- Real REST forwarding to tool servers (HTTPDispatcher) — next slice.
- Real MCP JSON-RPC forwarding (MCPDispatcher) — next slice.
- The toolyard or any tool containers.
- The Discord approver bot (already built in `../../discord-approver/`).
- `op-connect-shim` integration in the broker (the broker doesn't resolve any 1Password secrets).
- OPA / Rego policy engine.
- Multi-tenant or multi-user support.
- Off-host audit replication, JSONL export.
- Tailscale Serve / systemd deployment artifacts.
- mTLS or shared-secret auth between broker and tools.
- Anything in `agent-broker/` (the old monolith). Do not copy code from there.

## Notes for the implementer

- **FastAPI** is recommended. Pydantic v2 for request/response models. Uvicorn for the ASGI runner.
- **No async DB driver** — stdlib `sqlite3` is fine. Wrap blocking calls in `asyncio.to_thread` if needed inside async handlers. Performance is not a concern at home-lab scale.
- **The bot was built first.** Its `BrokerClient` interface is the de facto API contract for what this broker has to expose. If something in `../design/10-broker.md` is ambiguous, look at how the bot consumes the API (in `../../discord-approver/src/discord_approver/broker_client.py`) for the authoritative shape.
- **The `broker.*` op convention**: the approver profile uses synthetic ops like `broker.approve` to authorize approval-endpoint access. These are *not* dispatched as tool calls — they're a permission flag. The auth check happens in `api.py`; nothing downstream sees these ops.
- **Status transitions are one-way after terminal**: once a request is `completed`, `failed`, `rejected`, `expired`, or `denied`, it doesn't move. Defensive code in `approval.py` and `lifecycle.py` should refuse updates to terminal states.
- **Argument redaction is a defense-in-depth measure, not the primary control.** The right design is: tool servers receive arguments scoped by the broker's policy, and the policy never *passes* secret-shaped fields through. But until the registry has per-op argument schemas, regex redaction at the audit layer is the safety net.
- **`secrets.token_urlsafe(32)` produces a 43-character URL-safe base64 string.** That's the raw token. SHA-256 it for storage. Store the first 8 chars of the hex digest as `hash_prefix` (handy for operator commands like `revoke-token abc12345`).
- **Bind only to localhost** by default. Tailscale Serve is the external boundary, not the broker itself.
- **No background workers beyond the timeout reaper.** Don't introduce Celery, RQ, or any task queue. The lifecycle is synchronous within a request.
- **Keep this small.** Target is 500–800 LOC of Python. If you cross 1000 LOC excluding tests, stop and review what's bloating.

## When you're done

Original completion note requested:
- What was built and where.
- Anything that diverged from this plan and why.
- The exact `brokerctl` commands the operator needs to run for first-time setup (these will become the manual-testing doc's setup section if not already).
- Any open questions or follow-ups for the next slice (which will add real HTTPDispatcher + MCPDispatcher and start wiring the toolyard).

The next phase wires this broker to the toolyard + a real tool. Keep the `Dispatcher` protocol stable; that's the swap point.
