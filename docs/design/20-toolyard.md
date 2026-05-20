# Toolyard

Toolyard is the Docker lifecycle layer for Toolstack tools. The long-running
`toolyardd` process reads `tools/<id>/toolyard.yaml`, starts one Docker
container per enabled tool, injects initial secrets from 1Password Connect, and
mediates allowlisted writable secret updates.

Both the broker and toolyard read the same `toolyard.yaml` files. There is no
separate registry service.

## Responsibilities

- Discover and validate tool definitions.
- Build or pull Docker images.
- Start each tool bound to `127.0.0.1:<port>`.
- Fetch declared secrets from 1Password Connect at startup.
- Inject secret values directly into container tmpfs at `/run/secrets`.
- For fields declared `writable: true`, expose a per-tool Unix socket at
  `/run/toolyard/secrets.sock`.
- Enforce writable secret allowlists from the descriptor before patching
  1Password with the host-held read+write token.
- Notify the broker to reload its registry after lifecycle changes.
- Record toolyard-local audit events in `toolyard/state/toolyard-audit.jsonl`.

The toolyard does not authenticate agents, make broker policy decisions, or
mount 1Password tokens into tool containers.

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

1. Resolves each `(vault, item, field)` from 1Password Connect using the host
   read-only token.
2. Starts the container with a tmpfs mounted at `/run/secrets` and a tiny wait
   wrapper as PID 1.
3. Streams a tar archive of secret files into that tmpfs with `docker cp -`.
4. Writes `/run/secrets/.ready`, allowing the wrapper to exec the real app
   command.

The hydrated values are never written to persistent host storage.

## Writable Secret Updates

A writable field is an explicit capability in `toolyard.yaml`. A container does
not receive the 1Password write token. Instead, for any tool with writable
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
| `TOOLYARD_OP_CONNECT_HOST` | 1Password Connect URL |
| `TOOLYARD_OP_CONNECT_TOKEN_FILE` | Host read-only Connect token |
| `TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE` | Host read+write Connect token for allowlisted updates |
| `TOOLYARD_TOOLS_DIR` | Tool definitions, usually `/home/admin/toolstack/tools` |
| `TOOLYARD_STATE_DIR` | Toolyard audit/state directory |
| `TOOLYARD_RUNTIME_DIR` | Runtime sockets, usually `/run/toolstack/toolyardd` |
| `TOOLYARD_BROKER_RELOAD_URL` | Broker registry reload endpoint |
| `TOOLYARD_BROKER_RELOAD_TOKEN_FILE` | Broker token for registry reload |

## Commands

`toolyard` remains the operator CLI for validation, logs, and manual lifecycle
commands. Writable tools should be run through `toolyardd` so their per-tool
socket stays available.

```
toolyard validate ./tools/hello-rest
toolyard secrets hello-rest
toolyard ls --json
toolyard logs hello-rest --tail 100
```
