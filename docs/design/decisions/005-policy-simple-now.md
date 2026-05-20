# ADR 005: Simple per-profile ACL for v1; pluggable engine later

**Status**: Accepted (2026-05-16)

## Context

The old `agent-broker/` had OPA/Rego *and* a local-fallback Python evaluator running in parallel — two policy implementations to keep in sync, with overlapping responsibilities. For v1 of the rebuild, we want something simpler that:

- Does not lock in OPA.
- Does not preclude a more sophisticated engine later (OPA, Cedar, or an agent-in-the-loop evaluator).
- Covers home-lab use cases without ceremony.

## Decision

For v1: a per-profile YAML ACL evaluated by ~100 LOC of Python. The schema covers:

- `allowed_tools` / `denied_tools` — by tool ID
- `allowed_ops` / `denied_ops` — by `<tool>.<op>` pattern, glob-style
- `risk_classes`:
  - `read` → auto-allow
  - `write` → review-required
  - `destructive` → review-required, optionally with a shorter grant TTL
- `auto_grant_ttl_seconds` — how long a prompt-once approval grants similar future requests without re-prompting

Policy decisions return `{effect, reason, ttl_seconds}` — the same shape the old OPA decision used. The decision function is called from one place in the broker; the implementation behind it is opaque and swappable.

## Consequences

- No OPA dependency in the broker container.
- Policy files are human-readable, version-controlled, and PR-reviewable.
- Swap path: when we need composition, agent-in-the-loop evaluation, or cross-team policies, replace the decision function behind the same interface. No call-site changes.
- Limits: no policy composition across multiple authors, no per-resource scoping beyond what the tool's API already enforces, no complex condition chaining. Acceptable for home-lab scale.
- Adding a new policy rule is editing one YAML file and restarting the broker (or reloading on SIGHUP).

## Alternatives considered

- **OPA/Rego from day one**: powerful, well-understood, but the operational overhead exceeds home-lab value today. Pays off at multi-team, multi-policy-author scale. Deferred, not rejected.
- **Cedar from day one**: same as OPA — too much ceremony for home-lab now.
- **Pluggable engine framework from day one**: premature; YAGNI. We get the seam (single decision function) without the framework.
- **Agent-in-the-loop evaluator as v1**: interesting, but unproven. Trying to roll it in as the primary engine before the simpler path is built means we can't tell if the complexity is paying off.
- **Hard-coded Python rules**: too brittle. Every policy tweak becomes a code change.

## Notes for the future

The natural escalation path is:
1. Simple ACL (this ADR)
2. Add structured rule composition while keeping YAML (still in-process)
3. Swap to OPA / Cedar when home-lab graduates to multi-tenant or multi-author
4. Add an agent evaluator for cases where heuristic risk reasoning matters

Each step keeps the same decision contract. The broker doesn't have to know which engine is behind the interface.
