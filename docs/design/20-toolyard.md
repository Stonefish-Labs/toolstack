# Toolyard

Toolyard is the Docker lifecycle layer for Toolstack tools. The long-running
`toolyardd` process reads `toolyard.yaml` files from the configured tools root,
starts one Docker container per enabled tool, injects initial secrets from
Infisical, and mediates allowlisted writable secret updates.

Both the broker and toolyard read the same `toolyard.yaml` files. There is no
separate registry service. `BROKER_TOOLS_DIR` and `TOOLYARD_TOOLS_DIR` should
point at the same root.

## Responsibilities

- Discover and validate tool definitions.
- Build or pull Docker images.
- Start each tool bound to `127.0.0.1:<port>`.
- Fetch declared secrets from Infisical at startup.
- Inject secret values directly into container tmpfs at `/run/secrets`.
- For fields declared `writable: true`, expose a per-tool Unix socket at
  `/run/toolyard/secrets.sock`.
- Enforce writable secret allowlists from the descriptor before patching
  Infisical with the tool path's machine identity.
- Notify the broker to reload its registry after lifecycle changes.
- Record toolyard-local audit events in `${XDG_STATE_HOME:-~/.local/state}/toolstack/toolyard-audit.jsonl`.

The toolyard does not authenticate agents, make broker policy decisions, or
mount Infisical credentials into tool containers.

## Descriptor Schema

```yaml
id: media
type: rest                  # rest | mcp-http | mcp-stdio
entrypoint:
  build: .                  # or image: ghcr.io/example/tool:tag
  port: 4502
secrets:
  - name: refresh_token     # file at /run/secrets/refresh_token
    vault: ToolServer
    item: media
    field: REFRESH_TOKEN
    writable: true          # may be updated through /run/toolyard/secrets.sock
operations:
  - op: get_playback_state
    risk: read
  - op: refresh_oauth
    risk: write
```

`mcp-stdio`, custom volumes, and non-default networks remain schema-valid but
runtime-deferred.

## Runtime Secret Injection

For tools with any `secrets[]` entries, toolyardd:

1. Resolves each `(vault, item, field)` from Infisical. `vault` is the project,
   `item` is the secret path, and `field` is the secret key.
2. Starts the container with a tmpfs mounted at `/run/secrets` and a tiny wait
   wrapper as PID 1.
3. Streams a tar archive of secret files into that tmpfs with `docker cp -`.
4. Writes `/run/secrets/.ready`, allowing the wrapper to exec the real app
   command.

The hydrated values are never written to persistent host storage.

## Writable Secret Updates

A writable field is an explicit capability in `toolyard.yaml`. A container does
not receive an Infisical credential. Instead, for any tool with writable
fields, toolyardd mounts a per-tool socket directory at `/run/toolyard`.

Inside the container:

```bash
curl --unix-socket /run/toolyard/secrets.sock   -X POST http://toolyard/v1/secrets/refresh_token   -H 'Content-Type: application/json'   -d '{"value":"new-token","reason":"oauth refresh"}'
```

Toolyardd enforces that `refresh_token` is declared for that exact tool and has
`writable: true`. It then patches exactly the descriptor target field. It cannot
be asked by the container to create items, update arbitrary fields, or access a
different vault/item.

## Configuration

| Env | Purpose |
|---|---|
| `TOOLYARD_INFISICAL_HOST` | Infisical base URL |
| `TOOLYARD_INFISICAL_ENVIRONMENT` | Infisical environment slug, default `prod` |
| `TOOLYARD_INFISICAL_CREDENTIALS_DIR` | Per-path Universal Auth credentials, default `${XDG_CONFIG_HOME:-~/.config}/toolstack/infisical` |
| `TOOLYARD_INFISICAL_ORGANIZATION_SLUG` | Optional Infisical organization slug for Universal Auth login |
| `TOOLYARD_TOOLS_DIR` | Tool definitions, usually `/home/admin/.local/share/toolstack/tools` |
| `TOOLYARD_STATE_DIR` | Toolyard audit/state directory, default `${XDG_STATE_HOME:-~/.local/state}/toolstack` |
| `TOOLYARD_RUNTIME_DIR` | Runtime sockets, usually `/run/toolstack/toolyardd` |
| `TOOLYARD_BROKER_RELOAD_URL` | Broker registry reload endpoint |
| `TOOLYARD_BROKER_RELOAD_TOKEN_FILE` | Broker token for registry reload |

## Commands

`toolyard` remains the operator CLI for validation, logs, and manual lifecycle
commands. Writable tools should be run through `toolyardd` so their per-tool
socket stays available.

```
toolyard validate "$TOOLYARD_TOOLS_DIR/hello-rest"
toolyard secrets hello-rest
toolyard ls --json
toolyard logs hello-rest --tail 100
```
