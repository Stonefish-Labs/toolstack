# Toolyard

Toolyard is the Docker lifecycle runner and secret boundary for Toolstack tools.
The `toolyard` CLI validates descriptors and performs manual lifecycle tasks;
`toolyardd` is the long-running daemon used in deployment.

## What toolyardd does

- Reads `tools/<id>/toolyard.yaml`.
- Builds or pulls one Docker image per enabled tool.
- Starts one container per tool, bound to `127.0.0.1:<port>`.
- Fetches initial secrets from 1Password Connect.
- Injects secret values into container tmpfs at `/run/secrets`.
- Exposes `/run/toolyard/secrets.sock` for tools with `writable: true` fields.
- Uses the host-held read+write Connect token only after descriptor allowlist checks.

No 1Password token is mounted into tool containers.

## CLI

```sh
toolyard validate ./tools/hello-rest
toolyard secrets hello-rest
toolyard ls --json
toolyard logs hello-rest --tail 100
toolyard up time-mcp
```

Writable tools should be started by `toolyardd`, not a short-lived `toolyard up`,
so the per-tool writable-secret socket remains available.

## Configuration

```sh
export TOOLYARD_OP_CONNECT_HOST=http://127.0.0.1:19080
export TOOLYARD_OP_CONNECT_TOKEN_FILE=/home/admin/.config/toolstack/tokens/op-connect-read.token
export TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE=/home/admin/.config/toolstack/tokens/op-connect-readwrite.token
export TOOLYARD_TOOLS_DIR=/home/admin/toolstack/tools
export TOOLYARD_STATE_DIR=/home/admin/toolstack/toolyard/state
export TOOLYARD_RUNTIME_DIR=/run/toolstack/toolyardd
export TOOLYARD_BROKER_RELOAD_URL=http://127.0.0.1:8765/v1/registry/reload
export TOOLYARD_BROKER_RELOAD_TOKEN_FILE=/home/admin/.config/toolstack/tokens/broker-registry-admin.token
```

Deferred runtime surfaces remain: `mcp-stdio`, custom volumes, and non-default
networks.
