# 1Password Connect Python Shim

This folder is a small, dependency-free pattern for MCP servers and other Python tools that need secrets from the local 1Password Connect service.

The main idea: each tool gets a narrow Connect token, scoped to the vaults it needs. Read consumers get read-only tokens. Rotators or provisioners get a separate read-write token.

## Files

- `op_connect_shim.py`: single-file Python module using only the standard library.
- `op-connect-shim.env.example`: optional env-style config file.

## Configuration

Preferred resolution order:

1. Pass `host`, `token`, or `token_file` directly to `OnePasswordConnect`.
2. Use environment variables.
3. Use an env-style config file.

Supported variables:

```sh
OP_CONNECT_HOST=http://connect.your-tailnet.ts.net:19080
OP_CONNECT_TOKEN=replace-me
OP_CONNECT_TOKEN_FILE=/run/secrets/op-connect-token
OP_CONNECT_CONFIG=~/.config/op-connect-shim.env
```

For portability, do not hardcode the host in application code. Put it in `OP_CONNECT_HOST`, or use a config file such as:

```sh
mkdir -p ~/.config
install -m 600 op-connect-shim.env.example ~/.config/op-connect-shim.env
```

Then edit `~/.config/op-connect-shim.env` with the real host and token file path.

## Read Usage

```python
from op_connect_shim import OnePasswordConnect

op = OnePasswordConnect()

metrics_token = op.get_field(
    vault="Desktop-Services",
    item="desktop-dashboard",
    field="DESKTOP-METRICS",
)
```

Or with explicit config:

```python
from op_connect_shim import OnePasswordConnect

op = OnePasswordConnect(
    host="http://connect.your-tailnet.ts.net:19080",
    token_file="/run/secrets/desktop-services-read-token",
)
```

## Batch Field Reads

```python
fields = op.get_fields(
    vault="Desktop-Services",
    item="desktop-dashboard",
    fields=[
        "DESKTOP-SUNSHINE-CONTROL",
        "DESKTOP-METRICS",
        "DESKTOP-AI-VOICE-CONTROL",
    ],
)
```

## Write Usage

Use a separate token for writes. Most useful write workflows should use a read-write token, because updating an existing field by name requires reading vault, item, and field IDs first.

```python
from op_connect_shim import OnePasswordConnect

op_write = OnePasswordConnect(
    token_file="/run/secrets/some-service-readwrite-token",
)

op_write.update_field(
    vault="Some-Service",
    item="runtime-state",
    field="LAST_ROTATED_TOKEN",
    value="new-secret-value",
)
```

Creating a simple password item:

```python
op_write.create_password_item(
    vault="Some-Service",
    title="new-runtime-secret",
    fields={
        "API_TOKEN": "secret-value",
        "CREATED_BY": "mcp-server-name",
    },
    tags=["automation"],
)
```

## CLI Smoke Test

The module can also be run directly:

```sh
python3 op_connect_shim.py get Desktop-Services desktop-dashboard DESKTOP-METRICS
```

This prints the secret value, so use it only as a local smoke test.

## MCP Pattern

For a Python MCP server:

1. Mount or place a token file where only that service can read it.
2. Set `OP_CONNECT_HOST` and `OP_CONNECT_TOKEN_FILE` for that service.
3. Import `OnePasswordConnect` and fetch secrets at startup.
4. Keep fetched secrets in memory; do not log them.

Example container/service env:

```sh
OP_CONNECT_HOST=http://connect.your-tailnet.ts.net:19080
OP_CONNECT_TOKEN_FILE=/run/secrets/my-mcp-read-token
```

## Token Guidance

- Use one token per service or automation.
- Scope tokens to only the vaults that service needs.
- Use read-only tokens for consumers.
- Use separate read-write tokens for rotators/provisioners.
- Rotate tokens if they are printed, committed, copied into logs, or shared too broadly.
- Keep token files mode `600` when possible.

## Notes

1Password Connect tokens can be scoped per vault and limited to read or write permissions. Write endpoints exist for creating, replacing, deleting, and patching items, but read-only tokens will fail on write requests.
