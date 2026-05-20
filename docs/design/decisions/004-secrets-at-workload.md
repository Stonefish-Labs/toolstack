# ADR 004: Secrets At The Workload Boundary

Status: accepted, updated for toolyardd write proxy.

## Decision

Use one shared 1Password vault named `ToolServer`. Toolyardd owns the Connect
tokens on the host. Tool containers never receive a Connect token.

Initial reads:

- Tool descriptors declare `(vault, item, field)` references.
- Toolyardd resolves those references with a host read-only Connect token.
- Values are injected into container tmpfs at `/run/secrets/<name>`.
- Hydrated values are not stored persistently on the host.

Writable fields:

- A descriptor may mark individual fields with `writable: true`.
- Toolyardd exposes a per-tool Unix socket at `/run/toolyard/secrets.sock`.
- The container POSTs a new value for the declared secret name.
- Toolyardd validates the request against that exact tool descriptor and patches
  exactly the declared `(vault, item, field)` using the host read+write token.
- Undeclared fields, read-only fields, and cross-tool writes are denied and audited.

## Consequences

- The common case remains language-neutral: tools read files from `/run/secrets`.
- OAuth refresh-token rotation is supported without broad write tokens inside
  containers.
- The host read+write token is powerful, so toolyardd's allowlist checks and
  local audit log are part of the security boundary.
- Adding a new tool normally means adding one item to `ToolServer` and declaring
  its fields in `toolyard.yaml`; no per-tool Connect token is required.

## Rejected

- Mounting a shared Connect write token into writable containers. This made a
  compromised tool able to address more of the vault than its descriptor allowed.
- Per-tool Connect tokens. This gives stronger vault-level isolation but creates
  significant token sprawl for v1.
