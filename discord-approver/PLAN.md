# Implementation Plan: Discord Approver Bot

**Audience**: this is a hand-off plan for an implementer (likely another Claude model) who has not seen the prior conversation. Read the referenced design docs first — they have the architectural context.

## What you're building

A standalone Python service that bridges a broker's pending-approval queue to a human on Discord. For each pending approval request the broker has, the bot posts an embed in a configured Discord channel with four buttons (Approve / Approve+Note / Reject / Reject+Reason). Button clicks trigger modals where appropriate, then call the broker's approve/reject HTTP endpoints. The bot edits the original message as the request state changes (approved, rejected, expired, etc.).

The bot is one of four components in a larger toolserver system; this plan covers **only** the bot. The broker, toolyard, and tool servers will be built separately and are **not** in scope here. You'll build a small fake broker (FastAPI) as scaffolding so you can develop and demo the bot end-to-end without the real broker existing.

## Required reading

Before writing code, read these in order:

1. `../docs/trust-agents-with-action-not-access.md` — the system's thesis (skim).
2. `../docs/design/00-principles.md` — operational principles.
3. `../docs/design/01-architecture.md` — where the bot fits in the four-component shape.
4. `../docs/design/30-approver-discord.md` — **the spec for this component.** Read fully.
5. `../docs/design/decisions/006-discord-approval.md` — why Discord, why four buttons, what's deferred.
6. `../docs/design/10-broker.md` — sections "HTTP surface" and "Data model (SQLite)" only. You need to know what the broker's API will look like (so your fake broker matches it) and what an `action_request` row contains.

If anything in this plan conflicts with the design docs, the design docs win — flag the conflict and ask before deviating.

## Goal

A working bot that:

1. Posts approval cards to a Discord channel for each `pending_review` request reported by the (fake) broker.
2. Handles all four button interactions (Approve, Approve+Note, Reject, Reject+Reason) with appropriate modals.
3. Edits posted messages when request state transitions on the broker side (including transitions the bot didn't trigger — e.g., expiration).
4. Recovers cleanly from state-file loss (re-polls the broker, re-syncs).
5. Has a fake broker that another developer can run locally to exercise the bot without the real broker existing.

## Loose-coupling requirements

The point of building this in isolation is to validate the approval UX before the rest of the system is wired up. The receiving developer should be able to:

- **Test the embed visually without running Discord**: dump a sample embed to JSON for inspection, screenshot, or unit-test.
- **Test the reconciler logic without Discord and without a real broker**: inject mocks for both.
- **Swap the human surface later** (e.g., add ntfy alongside Discord) without rewriting the reconciler.

To enable this, three seams are mandatory:

1. **`BrokerClient` protocol/ABC** — the bot never talks HTTP directly. It calls `broker_client.list_pending()`, `broker_client.approve(...)`, etc. Implementations: `HTTPBrokerClient` (real) and `MockBrokerClient` (tests).
2. **`MessageStore` protocol/ABC** — small interface for the `request_id ↔ message_id` mapping. Implementations: `SqliteMessageStore` (real) and `InMemoryMessageStore` (tests).
3. **Pure embed builder** — `build_approval_embed(request, status) -> EmbedData` takes a request dataclass and returns either a `discord.Embed` *or* a plain dict that can be converted to a Discord embed. It must be testable with no discord.py runtime.

The Discord bot module (`bot.py`) is the thin shell that wires Discord events to the reconciler. The reconciler is where the real logic lives.

## Project layout

```
discord-approver/
├── PLAN.md                           # this file
├── README.md                         # write this as part of step 1
├── pyproject.toml                    # or requirements.txt — pick one
├── .gitignore                        # standard Python ignores + state/
├── src/discord_approver/
│   ├── __init__.py
│   ├── config.py                     # env var loading + validation
│   ├── models.py                     # Request dataclass, status enum
│   ├── broker_client.py              # BrokerClient ABC + HTTPBrokerClient + MockBrokerClient
│   ├── state.py                      # MessageStore ABC + SqliteMessageStore + InMemoryMessageStore
│   ├── embed.py                      # pure embed builder
│   ├── reconciler.py                 # polling loop + state sync
│   ├── bot.py                        # discord.py setup + button/modal handlers
│   └── cli.py                        # entry point: `discord-approver serve`
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # pytest fixtures
│   ├── test_embed.py                 # embed builder is pure — easiest to test first
│   ├── test_state.py                 # SqliteMessageStore against a temp DB
│   ├── test_broker_client.py         # HTTPBrokerClient against the fake broker
│   └── test_reconciler.py            # reconciler with all-mocks
├── scaffolding/
│   ├── README.md                     # how to run the fake broker
│   └── fake_broker.py                # FastAPI app emulating the broker
└── docs/
    └── manual-testing.md             # step-by-step: Discord setup → end-to-end check
```

## Module-by-module spec

### `models.py`

Pydantic models or dataclasses (your call — pick one and stay consistent).

```python
class RequestStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DENIED = "denied"
    COMPLETED = "completed"
    FAILED = "failed"

class Request:
    id: int
    caller: str              # e.g. "agent.hermes"
    profile: str             # e.g. "home-default"
    tool: str                # e.g. "media"
    op: str                  # e.g. "skip_track"
    arguments: dict          # secrets already stripped by broker
    reason: str | None
    status: RequestStatus
    risk: str                # "read" | "write" | "destructive"
    expires_at: int | None   # unix timestamp
    approver: str | None     # set after approve/reject
    decision_note: str | None
```

Match the fields the broker will return (see `docs/design/10-broker.md` data model). If the broker grows fields later, the bot should tolerate unknown ones rather than crash.

### `config.py`

Load env vars and fail fast if anything required is missing.

| Env var | Required | Default | Notes |
|---|---|---|---|
| `APPROVER_DISCORD_TOKEN_FILE` | yes | — | File containing the Discord bot token |
| `APPROVER_DISCORD_CHANNEL_ID` | yes | — | Integer Discord channel ID |
| `APPROVER_BROKER_URL` | yes | — | e.g. `http://127.0.0.1:8765` |
| `APPROVER_BROKER_TOKEN_FILE` | yes | — | File containing the bot's broker bearer token |
| `APPROVER_STATE_DIR` | no | `./state` | Where `messages.sqlite3` lives |
| `APPROVER_POLL_INTERVAL_SECONDS` | no | `10` | Float ok |

Read tokens from files at startup (don't keep file paths around — read the values once). Strip whitespace.

### `broker_client.py`

```python
class BrokerClient(Protocol):
    async def list_pending(self, after_id: int | None = None) -> list[Request]: ...
    async def get_request(self, request_id: int) -> Request | None: ...
    async def approve(self, request_id: int, approver: str, note: str | None) -> Request: ...
    async def reject(self, request_id: int, approver: str, reason: str | None) -> Request: ...
```

`HTTPBrokerClient`:
- Uses `httpx.AsyncClient` with the bearer token preset.
- Endpoints (match `docs/design/10-broker.md`):
  - `GET /v1/requests?status=pending_review&after_id=<n>`
  - `GET /v1/requests/<id>`
  - `POST /v1/requests/<id>/approve` with `{"approver": "<user>", "note": "<or null>"}`
  - `POST /v1/requests/<id>/reject` with `{"approver": "<user>", "reason": "<or null>"}`
- Retries with exponential backoff on 5xx. Honors 429 `Retry-After`. Logs and re-raises after N retries.
- Distinguishes broker-unreachable (network) from broker-rejection (4xx) errors.

`MockBrokerClient`:
- In-memory list of requests. Methods just manipulate the list.
- Useful for `test_reconciler.py`.

### `state.py`

```python
class MessageStore(Protocol):
    def upsert(self, request_id: int, message_id: int, status: str) -> None: ...
    def get(self, request_id: int) -> StoredMessage | None: ...
    def list_all(self) -> list[StoredMessage]: ...
    def delete(self, request_id: int) -> None: ...
```

`SqliteMessageStore`:
- SQLite schema:
  ```sql
  CREATE TABLE IF NOT EXISTS messages (
      request_id   INTEGER PRIMARY KEY,
      message_id   INTEGER NOT NULL,
      last_status  TEXT NOT NULL,
      posted_at    INTEGER NOT NULL,
      updated_at   INTEGER NOT NULL
  );
  ```
- `upsert` does INSERT OR REPLACE.
- Synchronous (SQLite is fine sync). Use a single connection with `check_same_thread=False`, or new connections per call — either works for our scale.

`InMemoryMessageStore`: dict-backed, for tests.

### `embed.py`

Pure function: takes a `Request` and returns a `discord.Embed`.

```python
def build_approval_embed(request: Request) -> discord.Embed:
    ...
```

But the function should be testable without booting discord.py — `discord.Embed` is a plain object you can construct and assert against (it has `to_dict()` for verification).

Embed layout:
- **Title**: `"Approval needed: {tool}.{op}"` for pending; `"{tool}.{op}"` for terminal states (with status indicator in description).
- **Color**:
  - Yellow (`0xFEE75C`) for `pending_review`
  - Green (`0x57F287`) for `approved`, `completed`
  - Red (`0xED4245`) for `rejected`, `expired`, `denied`, `failed`
  - Gray (`0x99AAB5`) for other terminal states
- **Fields**:
  - Caller (inline)
  - Profile (inline)
  - Risk (inline)
  - Reason (full width) — agent's stated reason
  - Arguments (full width, code block) — pretty-printed JSON
  - Decision (full width, only present when terminal) — "Approved by X: note" or "Rejected by X: reason" or "Expired (no decision)"
- **Footer**: `"Request #{id}"` + relative time (e.g., "Expires in 23h 55m" for pending, "Approved 2m ago" for terminal).

Argument redaction: the broker should strip secrets before sending, but as defense-in-depth, redact any field whose name matches `/password|token|secret|api_key|authorization/i` in the embed too.

Argument truncation: if the JSON is over ~800 chars, truncate with a "(truncated)" indicator. Discord embed fields have a 1024-char limit per field.

### `reconciler.py`

The polling loop. This is the actual logic of the bot.

Pseudocode:

```python
class Reconciler:
    def __init__(self, broker: BrokerClient, store: MessageStore, ui: ApprovalUI, poll_interval: float):
        ...

    async def run_forever(self):
        await self.startup_sync()
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.exception("reconciler tick failed")
            await asyncio.sleep(self.poll_interval)

    async def startup_sync(self):
        """On startup: post cards for any pending requests we don't have messages for; refresh stale messages."""
        ...

    async def tick(self):
        """One polling cycle."""
        # 1. Fetch new pending requests we haven't posted yet
        new_pending = await self.fetch_new_pending()
        for req in new_pending:
            message_id = await self.ui.post_card(req)
            self.store.upsert(req.id, message_id, req.status)

        # 2. Re-check status of requests we've already posted, edit messages on transition
        for stored in self.store.list_all():
            current = await self.broker.get_request(stored.request_id)
            if current is None or current.status != stored.last_status:
                await self.ui.edit_card(stored.message_id, current)
                if current:
                    self.store.upsert(stored.request_id, stored.message_id, current.status)
```

`ApprovalUI` is a small protocol the reconciler uses to talk to Discord:

```python
class ApprovalUI(Protocol):
    async def post_card(self, request: Request) -> int:
        """Return the Discord message_id."""
    async def edit_card(self, message_id: int, request: Request | None) -> None:
        """If request is None, the request no longer exists (rare). Otherwise update with current state."""
```

This is the seam that lets us swap Discord for ntfy later.

### `bot.py`

The discord.py-specific glue.

- Sets up a `discord.Client` with intents for message editing.
- On `on_ready`: instantiates the reconciler with `DiscordApprovalUI` (which implements `ApprovalUI`) and starts the loop as a background task.
- Registers persistent views (button handlers) so they survive restarts:
  - **Approve** → no modal; immediately calls `broker.approve(request_id, approver=user.name, note=None)`. Edit message on success.
  - **Approve+Note** → opens modal with optional `note` field. On submit: `broker.approve(..., note=note)`. Edit message.
  - **Reject** → opens modal with optional `reason` field (still allows reject-without-reason). On submit: `broker.reject(..., reason=reason)`. Edit message.
  - **Reject+Reason** → opens modal with **required** `reason` field. On submit: `broker.reject(..., reason=reason)`. Edit message.
- Approver identity = the Discord user's display name or username. Log both.
- Error handling: if broker call fails, ephemeral error message to the clicking user; the persistent message is unchanged so they can retry.

### `cli.py`

```python
def main():
    cfg = load_config()
    state = SqliteMessageStore(cfg.state_dir / "messages.sqlite3")
    broker = HTTPBrokerClient(cfg.broker_url, cfg.broker_token)
    bot = build_bot(cfg, state, broker)
    bot.run(cfg.discord_token)
```

Single entry point. Exit non-zero on config errors.

## Fake broker scaffolding

Build `scaffolding/fake_broker.py` as a small FastAPI app that:

1. Exposes the same endpoints the real broker will (per `docs/design/10-broker.md`):
   - `GET /v1/requests?status=...&after_id=...`
   - `GET /v1/requests/<id>`
   - `POST /v1/requests/<id>/approve`
   - `POST /v1/requests/<id>/reject`
2. Validates the bearer token against a configured value (read from `FAKE_BROKER_TOKEN_FILE` env var). Reject with 401 otherwise.
3. Adds dev-only endpoints (NOT in the real broker — clearly marked):
   - `POST /v1/_dev/inject` — body: a `Request` minus the id. Server assigns id, sets status to `pending_review`, returns the full request.
   - `POST /v1/_dev/expire/<id>` — force-transitions a pending request to `expired`.
   - `POST /v1/_dev/reset` — wipes all in-memory state.
4. In-memory state — restart resets everything. Print all state transitions to stdout for debugging.

Include a `scaffolding/README.md` with:
- How to start it: `uvicorn discord_approver.scaffolding.fake_broker:app --port 8765`
- How to inject a fake pending request: `curl -X POST ...`
- How to bulk-inject a few different risk classes (so the implementer can see how each color renders).

## Implementation order

This order lets you ship in vertical slices, each independently testable:

1. **Project setup** — folder, pyproject.toml, README, .gitignore. `uv` or `pip` install loop should work.
2. **`models.py`** — define the `Request` dataclass / Pydantic model and `RequestStatus` enum.
3. **`embed.py` + `test_embed.py`** — pure embed builder. Write tests covering each status color and the redaction logic.
4. **`state.py` + `test_state.py`** — SQLite + in-memory message stores.
5. **`broker_client.py` (mock only) + reconciler core + `test_reconciler.py`** — get the reconciler logic working with all mocks. This is the most important test target.
6. **Fake broker** — build `scaffolding/fake_broker.py`. Test it with curl.
7. **`HTTPBrokerClient` + `test_broker_client.py`** — point it at the fake broker. Verify all four methods round-trip.
8. **`bot.py`** — the discord.py shell. This is where you need real Discord credentials.
9. **`cli.py`** — wire it all together.
10. **Manual testing** — follow `docs/manual-testing.md` (which you write as part of step 1 or 10).

Stop and ask if anything breaks the loose-coupling rules (steps 3–5 must pass without ever importing discord.py).

## Testing

- All unit tests use pytest. Async tests use `pytest-asyncio`.
- The reconciler tests are the most important — they validate the bot's core behavior with no Discord and no real broker.
- The fake broker has its own quick smoke tests (just import + boot + make one request).
- The Discord bot itself (`bot.py`) is hardest to unit-test — keep it thin enough that manual testing is sufficient.

Target coverage: every function in `embed.py`, `state.py`, `broker_client.py`, and `reconciler.py` has at least one test. `bot.py` and `cli.py` don't need unit tests beyond what manual testing covers.

## Manual testing procedure

Write `docs/manual-testing.md` with detailed steps. At minimum it should cover:

1. **Discord setup** (one-time, manual):
   - Create a Discord application at https://discord.com/developers
   - Add a bot user, copy the token
   - In OAuth2 URL Generator: scope = `bot`, permissions = Send Messages + Embed Links + Manage Messages + Use External Emojis. Invite to your test server.
   - Create a private channel `#approver-test`. Get its ID (right-click → Copy Channel ID with developer mode).
2. **Local setup**:
   - Save the Discord token to a file (mode 600)
   - Save any string as the fake broker token to a file
   - Export env vars
3. **Run fake broker**: `uvicorn ...`
4. **Run bot**: `python -m discord_approver.cli serve`
5. **Inject test requests** (curl examples for each risk class + reason scenario):
   - Read-only Media call
   - Write-class Media call (with arguments visible)
   - Destructive-class call
6. **Verify each button**:
   - Approve → message turns green, footer shows approver, broker state confirmed via `GET /v1/requests/<id>`
   - Approve+Note → modal opens, note appears in edited message
   - Reject → modal opens with optional reason
   - Reject+Reason → modal opens with required reason; empty submission blocked
7. **Verify timeout/expire flow**:
   - Inject a request, force-expire it via `/v1/_dev/expire/<id>`, observe the bot edits the message within one poll cycle
8. **State recovery**:
   - Stop the bot. Delete `state/messages.sqlite3`. Restart. Confirm bot re-posts cards for outstanding pending requests.

## Acceptance criteria

- [ ] All unit tests pass (`pytest tests/`).
- [ ] Manual end-to-end test passes for all four buttons + expiration + state recovery.
- [ ] `embed.py`, `state.py` (both impls), `broker_client.py` (both impls), `reconciler.py` can be imported and tested without discord.py installed (verify by uninstalling it locally in a venv if you want — this is the loose-coupling acid test).
- [ ] Bot handles broker downtime: stop the fake broker, observe bot logs warnings and retries without crashing; restart broker, bot resumes.
- [ ] Bot handles Discord rate limits gracefully (test by injecting 10 requests at once).
- [ ] No secret values appear in logs (test by injecting a request with `arguments: {"password": "secret123"}` and verifying logs are clean).

## Out of scope (do not build)

- The real broker. Use the fake.
- Token issuance, profile ACLs, policy decisions — that's the broker's job.
- Bulk approval (`/approve-all` slash command).
- Threading (one Discord thread per request).
- DM approvals or multi-channel routing.
- ntfy as a parallel channel (the `ApprovalUI` seam makes this addable later — don't build it now).
- The toolyard or any tool containers.
- Caddy, Tailscale, systemd units (those come in deployment, later).
- Anything in `agent-broker/` — that's being archived. Do not lift code from it.

## Notes for the implementer

- **discord.py vs alternatives**: use `discord.py` (>= 2.4). It's the most mature and has good docs for buttons + modals. Don't use `py-cord` or older forks unless you have a reason.
- **Persistent views**: button handlers must be registered as persistent views (with `custom_id`s) so they survive bot restarts. Without this, buttons stop working after a restart.
- **Modal limits**: Discord modals support up to 5 fields. For our use case (one optional/required text field) this is fine.
- **Async**: use `httpx.AsyncClient` (not `requests`), since the bot is async-first.
- **Logging**: structured if you can. Always include `request_id` in log context. Don't log full argument payloads at INFO — DEBUG only.
- **Approver identity**: use `interaction.user.name` (the Discord username, not display name) for consistency with audit logs. Log both username and display name for ops convenience.
- **Color constants**: Discord's official brand colors are at https://discord.com/branding — match those if you want. The hex codes above are correct.
- **Don't over-abstract**: the four interfaces (BrokerClient, MessageStore, ApprovalUI, plus the pure embed function) are the *only* abstractions required. Don't add more. The Discord bot is a small program.

## When you're done

Update this PLAN.md (or write a follow-up `STATUS.md`) noting:
- What was built and where
- Anything that diverged from this plan and why
- Any open questions or follow-ups for v2

The next phase will wire this bot up to the real broker (per `docs/design/10-broker.md`). Keep the seam interfaces stable so that swap is a single-file change in `cli.py`.
