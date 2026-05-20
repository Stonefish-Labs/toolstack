# ADR 001: Token granularity is agent + profile

**Status**: Accepted (2026-05-16)

## Context

Bearer tokens authorize agent requests at the broker. Granularity choices:

1. One token per agent — agent selects a profile at request time.
2. One token per (agent, profile) — the token *is* the capability bundle.
3. One token per (agent, profile, tool) — finest-grained.

Granularity affects how many tokens we issue, how policy resolves, and how easy it is to scope an agent down or revoke its access.

## Decision

Each broker token is bound to one `(caller_id, profile)` pair. The token IS the capability bundle. If one agent needs two profiles, it gets two tokens.

## Consequences

- Token lookup is O(1) — no per-request profile selection logic.
- Policy resolution is straightforward: profile → ACL → allow/deny/review.
- An agent that needs multiple profiles juggles multiple tokens. Operational cost is small for home-lab scale.
- Profile changes require re-issuing the token. Acceptable: profiles change infrequently.
- Revocation is per-(caller, profile). Operators usually want exactly this granularity.
- Audit clearly shows which profile authorized each request without ambiguity.
- Bearer tokens remain the primary broker identity. High-value service profiles can add proof-of-possession defense in depth; the Discord approver profile uses optional HMAC signing with a separate shared secret.

## Alternatives considered

- **Per-tool tokens**: too fine. N tokens per agent multiplies distribution and revocation overhead. Per-tool authorization can still be expressed inside the profile (e.g., `denied_tools`).
- **One token, profile selected at request time**: more flexible (an agent could downgrade itself to a narrower profile), but easier to mis-issue (an agent could request a higher-privilege profile than intended). Worth revisiting if we add per-task profile elevation.
- **mTLS device identity with ambient profile**: better security shape (no bearer token to steal), but more setup overhead than the home-lab needs today. Reconsider if any agent host moves outside our control.
