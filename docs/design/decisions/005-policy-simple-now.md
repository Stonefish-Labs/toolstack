# ADR 005: Simple Caller Policies For V1

**Status**: Accepted (2026-05-21)

## Context

The broker needs a policy engine that is easy to understand and easy to edit
from the control panel. Reusable profile ACLs made simple tasks feel indirect:
operators thought in terms of callers, while the implementation asked them to
manage a second profile object.

## Decision

For v1, each caller owns one policy document in SQLite:

```json
{
  "tools": {
    "task-tool": {
      "operations": {
        "find_tasks": "allow",
        "add_tasks": "review",
        "delete_object": "deny"
      }
    }
  },
  "broker_ops": ["broker.list_requests"],
  "auto_grant_ttl_seconds": 3600
}
```

The broker evaluates exact operation entries. Missing tools and operations deny
by default. Broker control operations use glob matching so service callers can
hold narrow ops such as `broker.registry.reload` or broad ops such as
`broker.admin.*`.

The decision function remains a small Python seam. Future engines can replace
it without changing request lifecycle call sites.

## Consequences

- No OPA, Cedar, or profile loader dependency in the request path.
- The admin panel edits the real caller policy and can show operation
  descriptions from `toolyard.yaml`.
- Token refresh does not change policy; it revokes active tokens for the caller
  and issues a replacement.
- Policy repetition is acceptable at current scale. A template layer can be
  added later if repeated editing becomes painful, but templates should compile
  into caller policies rather than become a runtime requirement.

## Alternatives Considered

- **Reusable profiles**: clean for large fleets, but needless indirection here.
- **OPA/Rego or Cedar from day one**: powerful, but operationally heavier than
  the current deployment needs.
- **Hard-coded Python rules**: fast but too brittle; every policy tweak would be
  a code change.
- **Agent-in-the-loop evaluator**: interesting future direction, but not the
  primary authorization mechanism for v1.
