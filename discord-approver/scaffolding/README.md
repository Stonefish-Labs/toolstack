# Fake Broker Scaffolding

A small FastAPI app that emulates the broker's approval-related endpoints. Use it to develop and demo the Discord bot without the real broker.

## Quick Start

```bash
# From the discord-approver directory, with the venv activated:

# Set a token (any string works)
echo "dev-token" > /tmp/fake-broker.token
export FAKE_BROKER_TOKEN_FILE=/tmp/fake-broker.token

# Start the fake broker
uvicorn discord_approver.scaffolding.fake_broker:app --port 8765
```

The broker is now running at `http://127.0.0.1:8765`.

## Inject Test Requests

```bash
TOKEN="dev-token"

# Read-only demo call
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{
    "caller": "agent.hermes",
    "profile": "home-default",
    "tool": "hello-rest",
    "op": "greet",
    "risk": "read",
    "reason": "testing a read request"
  }' | python3 -m json.tool

# Write-class demo call (with arguments)
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{
    "caller": "agent.hermes",
    "profile": "home-default",
    "tool": "hello-rest",
    "op": "update_setting",
    "arguments": {"device_id": "abc-123", "direction": "forward"},
    "risk": "write",
    "reason": "user asked for a write action"
  }' | python3 -m json.tool

# Destructive-class call
curl -s -X POST http://127.0.0.1:8765/v1/_dev/inject \
  -H "Content-Type: application/json" \
  -d '{
    "caller": "agent.codex",
    "profile": "home-default",
    "tool": "demo-admin",
    "op": "delete_record",
    "arguments": {"project_id": "999", "name": "Old Project"},
    "risk": "destructive",
    "reason": "testing a destructive request"
  }' | python3 -m json.tool
```

## Verify Endpoints

```bash
# List pending requests
curl -s http://127.0.0.1:8765/v1/requests?status=pending_review \
  -H "Authorization: Bearer dev-token" | python3 -m json.tool

# Get a specific request
curl -s http://127.0.0.1:8765/v1/requests/1 \
  -H "Authorization: Bearer dev-token" | python3 -m json.tool

# Approve a request
curl -s -X POST http://127.0.0.1:8765/v1/requests/1/approve \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{"approver": "testuser", "note": "looks good"}' | python3 -m json.tool

# Force-expire a request
curl -s -X POST http://127.0.0.1:8765/v1/_dev/expire/2 | python3 -m json.tool

# Reset all state
curl -s -X POST http://127.0.0.1:8765/v1/_dev/reset | python3 -m json.tool
```

## Notes

- All state is in-memory — restart resets everything.
- State transitions print to stdout for debugging.
- Dev-only endpoints (`/v1/_dev/*`) are NOT in the real broker.
- The token defaults to `"dev-token"` if no file is configured.
