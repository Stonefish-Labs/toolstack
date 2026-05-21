# Manual Testing Guide

Step-by-step procedure for end-to-end testing the Discord Approver Bot.

## 1. Discord Setup (one-time)

1. Go to https://discord.com/developers/applications and create a new application.
2. Go to **Bot** → click **Add Bot** → copy the **Token**.
3. Under **Privileged Gateway Intents**, no extra intents are needed (we don't read message content).
4. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Manage Messages`, `Use External Emojis`
5. Copy the generated URL and open it in a browser to invite the bot to your test server.
6. Create a private channel (e.g., `#approver-test`).
7. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode).
8. Right-click the channel → **Copy Channel ID**.

## 2. Local Setup

```bash
cd discord-approver

# Save tokens to files (mode 600)
echo "YOUR_DISCORD_BOT_TOKEN" > /tmp/discord.token
chmod 600 /tmp/discord.token

echo "dev-token" > /tmp/broker.token
chmod 600 /tmp/broker.token

# Export env vars
export APPROVER_DISCORD_TOKEN_FILE=/tmp/discord.token
export APPROVER_DISCORD_CHANNEL_ID=YOUR_CHANNEL_ID
export APPROVER_BROKER_URL=http://127.0.0.1:8765
export APPROVER_BROKER_TOKEN_FILE=/tmp/broker.token
export APPROVER_POLL_INTERVAL_SECONDS=5
export FAKE_BROKER_TOKEN_FILE=/tmp/broker.token
```

## 3. Start Fake Broker

```bash
source .venv/bin/activate
uvicorn discord_approver.scaffolding.fake_broker:app --port 8765
```

## 4. Start the Bot

In a second terminal:

```bash
cd discord-approver
source .venv/bin/activate
export APPROVER_DISCORD_TOKEN_FILE=/tmp/discord.token
export APPROVER_DISCORD_CHANNEL_ID=YOUR_CHANNEL_ID
export APPROVER_BROKER_URL=http://127.0.0.1:8765
export APPROVER_BROKER_TOKEN_FILE=/tmp/broker.token
export APPROVER_POLL_INTERVAL_SECONDS=5

python -m discord_approver.cli
```

You should see the bot log in successfully.

## 5. Inject Test Requests

In a third terminal:

```bash
# Read-only request
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{"tool":"hello-rest","op":"greet","risk":"read","reason":"testing a read request"}'

# Write request
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{"tool":"demo-writer","op":"update_setting","arguments":{"device_id":"abc-123","direction":"forward"},"risk":"write","reason":"user asked for a write action"}'

# Destructive request
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{"tool":"demo-admin","op":"delete_record","arguments":{"project_id":"999"},"risk":"destructive","reason":"testing a destructive request"}'
```

Within one poll cycle (~5 seconds), approval cards should appear in the Discord channel.

## 6. Verify Each Button

### Approve (one-click)
1. Click **Approve** on any card.
2. ✅ Message should turn green immediately, buttons disappear, footer shows "Approved by YOUR_NAME".
3. Verify broker state: `curl -s http://127.0.0.1:8765/v1/requests/1 -H "Authorization: Bearer dev-token" | python3 -m json.tool`

### Approve+Note
1. Inject a new request and click **Approve+Note**.
2. ✅ A modal opens with an optional text field.
3. Enter a note and submit.
4. ✅ Message turns green, footer includes your note.

### Reject
1. Inject a new request and click **Reject**.
2. ✅ A modal opens with an optional reason field.
3. Submit without entering a reason (should work).
4. ✅ Message turns red, footer shows "Rejected by YOUR_NAME".

### Reject+Reason
1. Inject a new request and click **Reject+Reason**.
2. ✅ A modal opens with a **required** reason field.
3. Try to submit empty — Discord should block it.
4. Enter a reason and submit.
5. ✅ Message turns red, footer shows "Rejected by YOUR_NAME: YOUR_REASON".

## 7. Verify Timeout/Expire Flow

```bash
# Inject a request
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{"tool":"calendar","op":"create_event","risk":"write"}'

# Wait for the card to appear, then force-expire it
curl -s -X POST http://127.0.0.1:8765/v1/_dev/expire/REPLACE_WITH_ID
```

Within one poll cycle, the card should turn red with "Expired (no decision within timeout)".

## 8. Restart Recovery

1. Stop the bot (Ctrl+C).
2. Restart the bot.
3. ✅ The bot should reuse broker-backed message mappings and not duplicate already-posted pending cards.
4. Already-terminal requests should NOT get new cards.

## 9. Broker Downtime

1. Stop the fake broker (Ctrl+C on the uvicorn process).
2. ✅ The bot should log warnings about broker being unreachable.
3. ✅ The bot should NOT crash.
4. Restart the fake broker.
5. ✅ The bot should resume normal operation on the next tick.

## 10. Secret Redaction

```bash
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{"tool":"generic","op":"call_api","arguments":{"url":"https://api.example.com","password":"secret123","api_key":"sk-abc-456"},"risk":"write"}'
```

✅ The card should show `**REDACTED**` for `password` and `api_key` values.
✅ Bot logs should NOT contain "secret123" or "sk-abc-456".
