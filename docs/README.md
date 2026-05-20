# Toolstack Docs

Toolstack is a risk-management architecture for agent tools: agents get useful
actions, not broad access. The broker centralizes authorization, approval,
revocation, routing, and audit. Toolyard isolates tool execution and keeps
downstream secrets out of the agent host.

## Recommended Reading Order

1. [`trust-agents-with-action-not-access.md`](trust-agents-with-action-not-access.md)
   - the thesis and threat model.
2. [`design/01-architecture.md`](design/01-architecture.md) - the four-component
   system shape.
3. [`design/00-principles.md`](design/00-principles.md) - the operational rules
   behind the design.
4. [`user-guide.md`](user-guide.md) - how agents and operators use the system.
5. [`deployment/README.md`](deployment/README.md) - how the current deployment is
   assembled.

## Philosophy And Risk Model

- [`trust-agents-with-action-not-access.md`](trust-agents-with-action-not-access.md)
  is the project thesis: separate intent from authority.
- [`design/00-principles.md`](design/00-principles.md) turns the thesis into
  concrete design constraints.

## Architecture

- [`design/01-architecture.md`](design/01-architecture.md) explains the broker,
  Toolyard, Discord approver, tool containers, and trust boundaries.
- [`design/50-migration.md`](design/50-migration.md) records what was lifted,
  dropped, or deferred from the older monolithic approach.

## Component Specs

- [`design/10-broker.md`](design/10-broker.md) specifies broker auth, policy,
  request lifecycle, approval endpoints, and audit.
- [`design/20-toolyard.md`](design/20-toolyard.md) specifies tool lifecycle,
  container conventions, and descriptor handling.
- [`design/21-tool-template.md`](design/21-tool-template.md) shows how to build a
  server-side tool.
- [`design/22-agent-skill-convention.md`](design/22-agent-skill-convention.md)
  describes the matching thin client skill: broker-tool/profile config, stable
  CLI entry points, and direct broker action calls.
- [`design/30-approver-discord.md`](design/30-approver-discord.md) specifies the
  human approval surface.
- [`design/40-secrets.md`](design/40-secrets.md) explains per-tool secret
  resolution and writable secret updates.

## Operations

- [`user-guide.md`](user-guide.md) is the day-to-day operator and agent guide.
- [`deployment/README.md`](deployment/README.md) is the deployment walkthrough.
- [`end-to-end-testing.md`](end-to-end-testing.md) validates broker, Toolyard,
  tools, and approval flow together.

## Architecture Decisions

- [`design/decisions/001-token-granularity.md`](design/decisions/001-token-granularity.md)
- [`design/decisions/002-blind-jsonrpc-routing.md`](design/decisions/002-blind-jsonrpc-routing.md)
- [`design/decisions/003-docker-sandboxing.md`](design/decisions/003-docker-sandboxing.md)
- [`design/decisions/004-secrets-at-workload.md`](design/decisions/004-secrets-at-workload.md)
- [`design/decisions/005-policy-simple-now.md`](design/decisions/005-policy-simple-now.md)
- [`design/decisions/006-discord-approval.md`](design/decisions/006-discord-approval.md)

## History

[`history/`](history/) contains archived implementation plans from earlier build
slices. They are useful for context, but they are not current guidance. Prefer
the README files and active design docs above when operating or extending the
system.
