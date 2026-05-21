# Discord Approver Bot

A separate process that bridges the broker's pending-approval queue to a human on Discord. Posts approval cards in a configured channel, collects approve/reject via buttons and modals, edits the messages as state changes.

See [ADR 006](decisions/006-discord-approval.md).

## Architecture

```
Broker (HTTP API)
   ▲                          ▲
   │ GET /v1/requests?...     │ POST /v1/requests/<id>/approve
   │ (poll for pending)       │ POST /v1/requests/<id>/reject
   │                          │
   └─────────┬────────────────┘
             │
   ┌─────────▼─────────┐
   │  Discord Bot      │  (single process, discord.py)
   │                   │  - Polls broker every N seconds
   │                   │  - Posts approval embeds with 4 buttons
   │                   │  - Handles button interactions + modals
   │                   │  - Edits messages on state change
   │                   │  - Broker-backed request_id ↔ message_id mapping
   └─────────┬─────────┘
             │
        Discord API
             │
             ▼
       Channel #approvals
```

Single process. No local persistent state; the broker stores the
`request_id ↔ message_id` mapping in its SQLite database.

## Approval card

For each `pending_review` request, the bot posts an embed:

```
─────────────────────────────────────────────────
 Approval needed: media.skip_track
─────────────────────────────────────────────────
 Caller:    agent.hermes
 Tool:      demo-writer
 Operation: update_setting
 Risk:      write
 Reason:    user asked for a write action

 Arguments:
   key: example
   value: enabled

 Request #4271 · Expires in 23h 55m
─────────────────────────────────────────────────
 [Approve]  [Approve+Note]  [Reject]  [Reject+Reason]
─────────────────────────────────────────────────
```

Color encodes status:

| Color | Status |
|---|---|
| Yellow | `pending_review` |
| Green | `approved`, `completed` |
| Red | `rejected`, `expired`, `denied`, `failed` |
| Gray | terminal but informational |

## Interaction flow

### Approve (one-click)

1. User clicks **Approve**.
2. Bot calls `POST /v1/requests/4271/approve` with `{approver: "<user>", note: null}`.
3. Bot edits the embed: green color, footer becomes "Approved by <user> at <time>". Buttons disappear or are disabled.

### Approve+Note

1. User clicks **Approve+Note**.
2. Discord opens a modal with an optional text field for context.
3. User submits → bot calls `POST /v1/requests/4271/approve` with the note.
4. Bot edits the embed: green, footer includes the note.

### Reject

1. User clicks **Reject**.
2. Modal opens with an *optional* reason field. (The bot still allows reject-without-reason, but the modal exists so the user can add one in the same click.)
3. Submit → `POST /v1/requests/4271/reject` with `{approver, reason: <or null>}`.
4. Bot edits the embed: red, footer "Rejected by <user>: <reason>".

### Reject+Reason

Same as Reject but the modal requires the reason field (Discord-side validation).

Rejection reasons are surfaced back to the agent in the broker's action response, so the agent can adapt instead of retrying blindly.

## State transitions the bot needs to handle

The broker is the source of truth for request state. The bot must reconcile:

| Broker state | Bot action |
|---|---|
| `pending_review` (new, no message yet) | Post approval card |
| `approved` | Edit message: green, "Approved by ..." |
| `rejected` | Edit message: red, "Rejected by ..." |
| `expired` | Edit message: red, "Expired (no decision)" |
| `denied` | Should never appear (denied is a synchronous policy result, not pending). If it does, edit: red, "Policy denied" |
| `completed` | Optionally edit: green, "Completed" (depending on noise tolerance) |
| `failed` | Optionally edit: gray, "Failed: <error>" |

The bot polls the broker for transitions to update messages, including those triggered by the broker's own timeout reaper (no user interaction).

## Polling strategy

For v1, the bot polls:

```
GET /v1/requests?status=pending_review&after_id=<last_seen>
```

every `APPROVER_POLL_INTERVAL_SECONDS` (default 10).

It also periodically polls active (already-posted) request IDs in batches to detect server-side state changes (e.g., expired-by-timeout).

This is intentionally simple. Webhook delivery from the broker can be added later if poll latency becomes a problem.

### On bot startup

1. Read broker-backed `request_id ↔ message_id` mappings.
2. Poll broker for *all* `pending_review` requests.
3. For any pending request without a message in our state: post a new card.
4. For any message in our state whose request is no longer pending: edit it to reflect the current state.

This makes the bot recoverable from Discord bot restarts without a second
database: the broker is canonical.

## Configuration

Env vars:

| Var | Purpose |
|---|---|
| `APPROVER_DISCORD_TOKEN_FILE` | File containing the Discord bot token |
| `APPROVER_DISCORD_CHANNEL_ID` | Where to post approval cards |
| `APPROVER_BROKER_URL` | e.g. `http://127.0.0.1:8765` (broker is on the same host) |
| `APPROVER_BROKER_TOKEN_FILE` | Mounted file containing the bot's broker token (caller `bot.approver`) |
| `APPROVER_BROKER_SIGNING_SECRET_FILE` | File containing HMAC secret shared with the broker for signed approver calls |
| `APPROVER_ALLOWED_USER_IDS` | Comma-separated Discord user IDs allowed to approve/reject |
| `APPROVER_ALLOWED_ROLE_IDS` | Comma-separated Discord role IDs allowed to approve/reject |
| `APPROVER_POLL_INTERVAL_SECONDS` | Default 10 |

The Discord bot does not keep local persistent state. It records approval-card
message mappings through the broker, which stores them in broker SQLite state.

The bot reads its Discord token from the host-side file configured in
`DISCORD_APPROVER_TOKEN_FILE`.

## Broker-side setup

The bot needs a broker token to call approve/reject, plus a shared HMAC signing
secret if the broker has `BROKER_APPROVER_SIGNING_SECRET_FILE` configured.
Create a dedicated caller with approval broker ops:

```sh
brokerctl create-caller --name bot.approver \
  --broker-op broker.approve \
  --broker-op broker.reject \
  --broker-op broker.list_requests \
  --broker-op broker.audit \
  --broker-op broker.approval_messages.read \
  --broker-op broker.approval_messages.write
```

These special internal ops authorize approval HTTP endpoints
(`POST /v1/requests/<id>/approve` etc.), not forwardable tool actions.
`broker.registry.reload` belongs to the separate `svc.toolyard` caller, not the
Discord bot.

## Permissions on Discord

The bot is invited to a single private channel. Only Discord users whose ID is in `APPROVER_ALLOWED_USER_IDS` or who have a role in `APPROVER_ALLOWED_ROLE_IDS` may approve, reject, submit approval modals, or run `/clear`. The bot needs these channel permissions:

- Send Messages
- Embed Links
- Use External Emojis (cosmetic only)
- Manage Messages (for deleting completed cards during `/clear`)
- Read Message History (for `/clear` history scanning)

No server-wide permissions. No DM permissions.

## Error handling

| Failure | Bot behavior |
|---|---|
| Discord 429 (rate limit) | Standard backoff; queue posts |
| Discord 5xx | Retry with backoff; log; broker timeout still triggers |
| Broker unreachable | Retry; log loudly; bot is dead-in-the-water without broker but doesn't crash |
| Modal submission with invalid data | Discord enforces client-side; broker validates server-side and returns 400; bot reports error in the channel |
| Broker message mapping missing | Re-post any still-pending request on startup; broker request state remains canonical |

## What's deliberately small

The bot is not a generic Discord framework:

- One channel.
- Four buttons.
- One kind of embed.
- No slash commands in v1 (no `/approve <id>`; the buttons are the UX).
- No private DM approvals.
- No persistent threading per request.
- No analytics dashboard.

Bulk approval (`/approve-all`, batching by tool) is a clear v2 if volume justifies it, but starts simple.

## Replacement channels

Because the bot is a separate process, you can:

- Replace Discord with a custom mobile push channel later by adding a new broker-backed message surface.
- Add a desktop menu-bar app later using the same broker API.

V1 intentionally supports one active approval surface per request.

The broker's API is the stable contract; the human surface is interchangeable.
