# Discord Approver Bot

A standalone Python service that bridges a broker's pending-approval queue to a human on Discord. Part of the [toolserver](../docs/design/01-architecture.md) system.

## What It Does

For each `pending_review` action request from the broker, the bot:

1. Posts an approval card in a configured Discord channel with four buttons:
   - **Approve** — one-click approval
   - **Approve+Note** — approve with an optional audit note
   - **Reject** — reject with an optional reason
   - **Reject+Reason** — reject with a required reason
2. Edits the card when the request state changes (approved, rejected, expired, etc.)
3. Recovers cleanly from state loss by re-polling the broker

## Quick Start

```bash
# Install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Set up config (see docs/manual-testing.md for full guide)
echo "YOUR_DISCORD_TOKEN" > /tmp/discord.token
echo "YOUR_BROKER_TOKEN" > /tmp/broker.token

export APPROVER_DISCORD_TOKEN_FILE=/tmp/discord.token
export APPROVER_DISCORD_CHANNEL_ID=YOUR_CHANNEL_ID
export APPROVER_BROKER_URL=http://127.0.0.1:8765
export APPROVER_BROKER_TOKEN_FILE=/tmp/broker.token
export APPROVER_ALLOWED_USER_IDS=YOUR_DISCORD_USER_ID
# Optional when the real broker requires signed approver requests:
# export APPROVER_BROKER_SIGNING_SECRET_FILE=/tmp/broker-approver-signing.key

# Start the fake broker (for development)
uvicorn discord_approver.scaffolding.fake_broker:app --port 8765

# Start the bot
python -m discord_approver.cli
```

## Configuration

| Env Var | Required | Default | Description |
|---------|----------|---------|-------------|
| `APPROVER_DISCORD_TOKEN_FILE` | ✅ | — | File containing Discord bot token |
| `APPROVER_DISCORD_CHANNEL_ID` | ✅ | — | Discord channel ID for approval cards |
| `APPROVER_BROKER_URL` | ✅ | — | Broker HTTP base URL |
| `APPROVER_BROKER_TOKEN_FILE` | ✅ | — | File containing broker bearer token |
| `APPROVER_BROKER_SIGNING_SECRET_FILE` | ❌ | — | File containing HMAC signing secret for broker calls |
| `APPROVER_ALLOWED_USER_IDS` | ✅* | — | Comma-separated Discord user IDs allowed to decide requests |
| `APPROVER_ALLOWED_ROLE_IDS` | ✅* | — | Comma-separated Discord role IDs allowed to decide requests |
| `APPROVER_POLL_INTERVAL_SECONDS` | ❌ | `10` | Polling interval in seconds |

`*` At least one allowed user ID or role ID is required.

## Architecture

```
Broker (HTTP API)  ◄──►  Reconciler  ◄──►  Discord (via discord.py)
                              │
                         MessageStore
                         (broker-backed)
```

Key seams for testability and swappability:
- **`BrokerClient`** protocol — swap HTTP for mocks in tests
- **`MessageStore`** protocol — SQLite or in-memory
- **`ApprovalUI`** protocol — swap Discord for ntfy or other surfaces
- **Pure embed builder** — testable without discord.py runtime

## Testing

```bash
# All unit tests
pytest tests/ -v

# Just the core logic (no discord.py needed)
pytest tests/test_reconciler.py tests/test_state.py tests/test_broker_client.py -v
```

See [docs/manual-testing.md](docs/manual-testing.md) for the full end-to-end testing procedure.

## Project Layout

```
src/discord_approver/
├── models.py          # Request dataclass, status enum
├── config.py          # Env var loading + validation
├── broker_client.py   # BrokerClient protocol + HTTP + mock implementations
├── state.py           # MessageStore protocol + SQLite + in-memory
├── embed.py           # Pure embed builder + 4-button view
├── reconciler.py      # Polling loop + state sync
├── bot.py             # Discord.py shell + button/modal handlers
├── cli.py             # Entry point
└── scaffolding/
    └── fake_broker.py # FastAPI fake broker for development
```
