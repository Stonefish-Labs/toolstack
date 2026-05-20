# Manual Testing Guide

Step-by-step verification of the broker service.

## Prerequisites

```bash
cd broker/
source .venv/bin/activate
```

## 1. Setup

```bash
# Initialize the database
brokerctl init-db
# Expected: "database initialized at ./state/broker.sqlite3"

# Verify policy profiles load
brokerctl reload-registry
# Expected: "registry reloaded: 0 tool(s), 3 profile(s)"
```

## 2. Token Creation

```bash
# Create an agent caller
brokerctl create-caller --name agent.hermes --profile home-default
# Save the printed BEARER TOKEN

# Create a bot caller
brokerctl create-caller --name bot.approver --profile approver
# Save the printed BEARER TOKEN

# Verify callers exist
brokerctl list-callers
# Expected: both callers listed

# Set tokens for subsequent commands
export AGENT_TOKEN="<paste agent token>"
export BOT_TOKEN="<paste bot token>"
```

## 3. Smoke Test (curl)

```bash
# Start the broker in a separate terminal
brokerctl serve

# Test health (no auth needed)
curl -s http://127.0.0.1:8765/v1/health
# Expected: {"ok": true}

# Test allowed action (read)
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/actions/hello-rest.greet \
  -d '{"arguments": {}, "reason": "smoke test"}'
# Expected: 200 with synthetic result

# Test review-required action (write)
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/actions/calendar.create_event \
  -d '{"arguments": {}, "reason": "create test event"}'
# Expected: 202 with request_id and status=pending_review

# Test denied action
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/actions/admin.do_stuff \
  -d '{"arguments": {}}'
# Expected: 403

# Test failure path
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/actions/hello-rest.greet \
  -d '{"arguments": {"__synthetic_outcome": "fail"}}'
# Expected: 502

# Test invalid token
curl -s -X POST \
  -H "Authorization: Bearer invalid-token" \
  http://127.0.0.1:8765/v1/actions/hello-rest.greet \
  -d '{"arguments": {}}'
# Expected: 401
```

## 4. Approval Flow (CLI)

```bash
# Create a pending request
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/actions/calendar.create_event \
  -d '{"arguments": {}, "reason": "test approval"}'
# Note the request_id from the response

# List pending requests
brokerctl list-requests --status pending_review

# Approve it
brokerctl approve <request_id> --approver test --note "manual approval"
# Expected: status transitions to completed

# Check audit trail
brokerctl audit --limit 20
```

## 5. Timeout Flow

```bash
# Restart broker with short timeout
BROKER_APPROVAL_TIMEOUT_SECONDS=10 brokerctl serve

# Create a pending request
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/actions/calendar.create_event \
  -d '{"arguments": {}}'

# Wait 15 seconds, then check
sleep 15
brokerctl list-requests --status expired
# Expected: the request should be there

# Try to approve the expired request via HTTP
curl -s -X POST \
  -H "Authorization: Bearer $BOT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/requests/<request_id>/approve \
  -d '{"approver": "test"}'
# Expected: status remains "expired"
```

## 6. Token Revocation

```bash
# List tokens to get the hash prefix
brokerctl list-tokens

# Revoke a token
brokerctl revoke-token <hash-prefix>

# Try to use the revoked token
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  http://127.0.0.1:8765/v1/actions/hello-rest.greet \
  -d '{"arguments": {}}'
# Expected: 401
```

## 7. Bot Integration

The headline test — run the Discord bot against this broker:

```bash
# 1. Stop any fake_broker.py instances

# 2. Configure the bot
cd ../discord-approver/
# Export env vars or write token files outside the repository:
#   APPROVER_BROKER_URL=http://127.0.0.1:8765
#   APPROVER_BROKER_TOKEN_FILE=/tmp/toolstack-broker.token
# Write the bot.approver raw token to /tmp/toolstack-broker.token

# 3. Start the broker (in broker/ dir)
cd ../broker/
brokerctl serve

# 4. Start the bot (in discord-approver/ dir)
cd ../discord-approver/
source .venv/bin/activate
python -m discord_approver

# 5. From another terminal, trigger a review-required action
curl -s -X POST \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/v1/actions/calendar.create_event \
  -d '{"arguments": {"device_id": "abc-123"}, "reason": "user asked for a write action"}'

# 6. Verify the bot posts a card in Discord
# 7. Click Approve → broker transitions to completed → bot edits the message
# 8. Repeat for Reject+Reason; verify the reason is stored
# 9. Force-expire via short timeout → bot edits the message to "Timed out"
```

This is the slice's win condition: the same bot that worked against the
fake broker now works against the real one with zero code changes.
