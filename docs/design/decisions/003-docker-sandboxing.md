# ADR 003: Docker per tool for isolation

**Status**: Accepted (2026-05-16)

## Context

Tool servers should be isolated from each other and from the broker process. The agent must not be able to reach a tool server's filesystem, env, or memory even if a tool server is compromised. Options:

- Docker container per tool
- Firejail / bwrap sandboxes
- systemd-nspawn
- Native subprocess with chroot
- No sandboxing (the old `agent-broker/` default for in-process connectors)

The old `agent-broker/` used a mix — systemd transient units for `sandbox-job` workloads, raw `subprocess.Popen` for MCP stdio, and no isolation for in-process Python connectors. Inconsistent, and the loose options limited what we could express per tool.

## Decision

Each tool runs in a Docker container managed by the toolyard. Containers bind to `127.0.0.1:<port>` only; the broker is the only thing that addresses them.

Uniform pattern regardless of tool language or runtime. Tools that are not naturally HTTP/JSON-RPC are wrapped in a thin server inside their container (e.g., an `mcp-stdio` tool wrapped by a small adapter that exposes `mcp-http`).

## Consequences

- Free: filesystem isolation, network namespace, env isolation, image versioning, easy restart.
- Tool definition (`toolyard.yaml`) can declare volumes, networks, and capabilities. This opens the door to future patterns like:
  - Ephemeral on-demand containers
  - Mounted SMB shares for tools that need filesystem access to specific paths
  - Per-tool network egress policies via Docker networks
- Cold start cost: ~1–3 seconds per container restart. Acceptable for development workflow; tools are long-running in normal operation.
- Operational dependency on the Docker daemon (already present on the target VM).
- Container images need to be built or pulled. A small build step on tool changes is acceptable in exchange for reproducibility and isolation.

## Alternatives considered

- **Firejail / bwrap**: lighter, but inconsistent per language/runtime. Profile management becomes tool-specific. No clear win for our environment.
- **systemd-nspawn**: similar isolation to Docker but smaller ecosystem and fewer ergonomic tools. No advantage that outweighs Docker's familiarity.
- **Raw subprocess + chroot**: brittle, no network isolation, doesn't compose with future SMB-mount or on-demand patterns. Rejected.
- **No sandboxing**: violates principle 1 (the agent should not be able to reach past tool boundaries even through a compromised tool). Rejected.
