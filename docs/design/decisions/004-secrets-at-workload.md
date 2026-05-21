# ADR 004: Secrets At The Workload Boundary

Status: accepted, updated for Infisical-backed toolyardd write proxy.

## Decision

Use one shared Infisical project named `ToolServer`. Toolyardd owns per-path
Universal Auth machine identity credentials on the host. Tool containers never
receive Infisical credentials.

Initial reads:

- Tool descriptors declare `(vault, item, field)` references, where `vault` is
  the Infisical project, `item` is the secret path, and `field` is the key.
- Toolyardd resolves those references with the machine identity for that path.
- Values are injected into container tmpfs at `/run/secrets/<name>`.
- Hydrated values are not stored persistently on the host.

Writable fields:

- A descriptor may mark individual fields with `writable: true`.
- Toolyardd exposes a per-tool Unix socket at `/run/toolyard/secrets.sock`.
- The container POSTs a new value for the declared secret name.
- Toolyardd validates the request against that exact tool descriptor and patches
  exactly the declared `(vault, item, field)` using that path's machine identity.
- Undeclared fields, read-only fields, and cross-tool writes are denied and audited.

## Consequences

- The common case remains language-neutral: tools read files from `/run/secrets`.
- OAuth refresh-token rotation is supported without broad credentials inside
  containers.
- The host machine identities are powerful for their paths, so toolyardd's
  allowlist checks and local audit log are part of the security boundary.
- Adding a new tool normally means adding one Infisical path to `ToolServer`,
  adding one local machine-identity file, and declaring its fields in
  `toolyard.yaml`.

## Rejected

- Mounting Infisical credentials into writable containers. This made a
  compromised tool able to address more secrets than its descriptor allowed.
- One shared write-capable machine identity. This is operationally simple but
  weaker than path-scoped identities.
