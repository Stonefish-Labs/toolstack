# Secrets

Toolstack keeps 1Password Connect tokens on the host. Tool containers never get
a Connect token. Initial secret values are hydrated by `toolyardd` and injected
into the container's `/run/secrets` tmpfs; writable fields are updated through a
per-tool Unix socket that enforces the descriptor allowlist.

## Vault Convention

Use one shared 1Password vault named `ToolServer` for tool-managed secrets.
Each tool gets one item, usually named after the tool ID.

```
ToolServer
  hello-rest
    API_KEY
  media
    CLIENT_ID
    CLIENT_SECRET
    REFRESH_TOKEN
```

## Descriptor References

```yaml
secrets:
  - name: api_key
    vault: ToolServer
    item: hello-rest
    field: API_KEY
```

If `vault` is omitted, the default is `ToolServer`. If `item` is omitted, the
item defaults to the tool ID.

## Initial Hydration

At startup, `toolyardd` resolves each declared secret with the host read-only
Connect token, starts the container with `/run/secrets` as tmpfs, and streams
only that tool's secret files into the tmpfs. No hydrated secret values are
written to persistent host storage.

Tool code reads files normally:

```python
def secret(name: str) -> str:
    with open(f"/run/secrets/{name}", encoding="utf-8") as f:
        return f.read().strip()
```

## Writable Fields

Writable fields support OAuth refresh-token rotation and similar cases. The
capability is opt-in per field:

```yaml
secrets:
  - name: refresh_token
    field: REFRESH_TOKEN
    writable: true
```

A writable tool receives `/run/toolyard/secrets.sock`, not a 1Password token.
To update the field:

```bash
curl --unix-socket /run/toolyard/secrets.sock   -X POST http://toolyard/v1/secrets/refresh_token   -H 'Content-Type: application/json'   -d '{"value":"NEW_REFRESH_TOKEN","reason":"oauth refresh"}'
```

Toolyardd checks the request against that tool's descriptor and patches exactly
`(vault, item, field)` from the matching `writable: true` entry. Undeclared
fields, read-only fields, and cross-tool names are denied and audited.

## Host Tokens

Store tokens under `/home/admin/.config/toolstack/tokens` with mode `0600`:

- `op-connect-read.token` - read-only access to `ToolServer`
- `op-connect-readwrite.token` - read+write access to `ToolServer`

The read+write token is only used by toolyardd after descriptor allowlist
checks. It is never mounted into tool containers.

## Rotation

- Upstream credential changed by operator: update 1Password, then restart
  `toolyardd` or the affected tool so initial hydration picks it up.
- Credential changed by the tool: POST the new value to `/run/toolyard/secrets.sock`.
  The next restart hydrates the latest value from 1Password.
- Connect token rotation: replace the host token file and restart `toolyardd`.
