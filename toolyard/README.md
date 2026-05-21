# Toolyard

Toolyard is the Docker lifecycle runner and secret boundary for Toolstack tools.
The `toolyard` CLI validates descriptors and performs manual lifecycle tasks;
`toolyardd` is the long-running daemon used in deployment.

## What toolyardd does

- Reads `tools/<id>/toolyard.yaml`.
- Builds or pulls one Docker image per enabled tool.
- Starts one container per tool, bound to `127.0.0.1:<port>`.
- Fetches initial secrets from Infisical.
- Injects secret values into container tmpfs at `/run/secrets`.
- Exposes `/run/toolyard/secrets.sock` for tools with `writable: true` fields.
- Uses per-tool Infisical machine identities only after descriptor allowlist checks.

No Infisical credential is mounted into tool containers.

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
export TOOLYARD_INFISICAL_HOST=https://infisical.internal.example:19081
export TOOLYARD_INFISICAL_ENVIRONMENT=prod
export TOOLYARD_INFISICAL_CREDENTIALS_DIR=/home/admin/.config/toolstack/infisical
export TOOLYARD_TOOLS_DIR=/home/admin/toolstack/tools
export TOOLYARD_STATE_DIR=/home/admin/.local/state/toolstack
export TOOLYARD_RUNTIME_DIR=/run/toolstack/toolyardd
export TOOLYARD_BROKER_RELOAD_URL=http://127.0.0.1:8765/v1/registry/reload
export TOOLYARD_BROKER_RELOAD_TOKEN_FILE=/home/admin/.config/toolstack/tokens/broker-registry-admin.token
```

Each tool path uses one local machine-identity file in
`TOOLYARD_INFISICAL_CREDENTIALS_DIR`, for example
`my-tool.env` for Infisical path `/my-tool`.

Deferred runtime surfaces remain: `mcp-stdio`, custom volumes, and non-default
networks.
