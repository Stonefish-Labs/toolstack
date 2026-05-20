# ADR 006: Discord bot for human approval

**Status**: Accepted (2026-05-16)

## Context

Sensitive actions require human approval. The approval UX must:

- Be reachable from a phone, not just a desktop.
- Capture optional notes on approval and required reasons on rejection.
- Show the *operation* being approved, not the command (principle 7).
- Survive broker restarts (state lives in the broker, not the bot).
- Be independently deployable from the broker.

The old `agent-broker/` had an embedded HTML approval page at `/approval` (200+ lines of HTML as a Python string in `api.py`) plus optional ntfy push notifications. The HTML page is unmaintainable; ntfy doesn't support rich interactions (you can't capture a reason inline).

## Decision

A Discord bot, running as a separate process, is the approval interface for v1. Approval authority is limited to configured Discord user IDs and role IDs; every approve/reject path checks the allowlist before calling the broker.

For each `pending_review` request, the bot posts a channel message with four buttons:

| Button | Behavior |
|---|---|
| **Approve** | Approves immediately. No prompt. |
| **Approve + Note** | Opens modal for optional context. Submits to broker. |
| **Reject** | Opens modal with optional reason field. |
| **Reject + Reason** | Opens modal with required reason field. |

Rejection reasons are surfaced back to the agent in the action response so the agent can adapt instead of retrying blindly.

The bot subscribes to broker pending-review events. For v1, this is polling (`GET /v1/requests?status=pending_review` every N seconds). Webhook delivery can be added later if polling latency matters. Broker calls use the bot's bearer token and, when configured, HMAC request signing with timestamp and nonce headers.

On any state transition (approved, rejected, timed-out, expired), the bot edits the original Discord message to show the outcome and the actor.

## Consequences

- Free: mobile push, message history, rich UI primitives, multi-device access. Discord handles the parts that would otherwise be a custom mobile app.
- Approval queue is a Discord channel. Backlog handling is "scroll through unread." Acceptable until volume justifies batching or a `/approve-all` slash command (deferred).
- Bot is independently deployable and replaceable. We can add ntfy as a parallel channel later, or swap to a custom mobile push, without touching the broker.
- Discord outage = approvals stuck. Acceptable for home-lab: broker timeout still triggers and fail-closed transitions to `expired`.
- Two bot-side secrets to manage: the Discord bot token and the broker approver token. Deployments that enable HMAC signing also store a shared approver signing secret on the broker and approver hosts.

## Alternatives considered

- **macOS menu-bar app** (the old `agent-secrets-work` pattern): device-bound. Doesn't work when away from desk. Useful as a future *additional* surface, not as the primary one.
- **Embedded web UI** (old broker's `/approval` page): forces desktop browser access. Embedding HTML in Python is unmaintainable. Rejected.
- **ntfy only**: great push, but no rich interaction surface — can't easily collect a reason. Useful as an alerting backup; not viable as the only approval path.
- **Slack**: equivalent capability. User is on Discord. No reason to prefer Slack.
- **Custom mobile app**: too much investment for v1. Discord covers the same need.

## Notes for implementers

- Bot needs a single channel ID configured. Multi-channel routing (by risk class, by tool) is deferred.
- Bot must be configured with at least one allowed Discord user ID or role ID; names are not accepted because Discord snowflake IDs are stable.
- Message embeds should never include resolved secret values. The broker's audit-redaction rules apply equally to anything the bot displays.
- The mapping of `request_id ↔ message_id` lives in a tiny SQLite or JSON file on the bot host. If lost, the bot can repost cards on next poll; the old messages just become stale and get edited to "expired" on timeout.
