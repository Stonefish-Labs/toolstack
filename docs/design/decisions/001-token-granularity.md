# ADR 001: Token Granularity Is One Caller

**Status**: Accepted (2026-05-21, supersedes the older caller+profile shape)

## Context

Bearer tokens authorize requests at the broker. Earlier designs bound tokens to
`caller + profile`, where a reusable profile was the capability bundle. In
practice, callers are concrete identities (`agent.kira`, `svc.approver`,
`svc.broker-panel`) and reusable profiles added an extra concept without much
benefit for this home-lab scale.

## Decision

Each broker token belongs to exactly one caller. The caller owns its policy
directly in SQLite. There is no request-time profile selection and no reusable
profile abstraction in the admin API or panel.

Refreshing a token means revoking the caller's active token rows and issuing one
replacement token for the same caller.

## Consequences

- Token lookup remains O(1): token hash to caller.
- Policy resolution is direct: caller to caller policy.
- Audit clearly shows which caller requested the action.
- Operators edit the real caller's enabled tool operations, with descriptions
  from `toolyard.yaml`.
- Reusable role templates can be added later if repeated policy editing becomes
  painful, but they are not part of the runtime model.

## Alternatives Considered

- **Reusable profiles**: useful for large fleets, but too abstract here and easy
  to confuse with caller identity.
- **Per-tool tokens**: precise but multiplies token distribution and revocation.
- **One token with caller-selected role**: flexible but weakens issuance safety.
- **mTLS device identity**: attractive, but more setup overhead than bearer
  tokens on a tailnet.
