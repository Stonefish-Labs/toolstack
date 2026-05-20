# Principles

The toolserver project is built around one thesis from [`trust-agents-with-action-not-access.md`](../trust-agents-with-action-not-access.md):

> Trust agents with action, not access.

The agent expresses intent. The broker decides authority. Tool servers execute. Downstream systems are where real authority lands. The agent can reach the broker. It cannot reach tool servers or downstream systems directly.

These are the operational principles that translate the thesis into design constraints. Every architectural choice in this project should be defensible against this list.

## 1. The agent cannot rewrite the boundary

The broker, tool servers, and secret sources run on infrastructure the agent has no execution access to. No SSH from the agent host into the tool VM. No shared keychain. No file paths the agent can read. The Tailscale tunnel is the only path in, and the broker is the only thing on the other end of that path that the agent can address.

If a compromised agent host gives an attacker enough authority to perform a destructive operation directly, the boundary is wrong.

## 2. Intent in, decisions out

The agent sends *what* it wants done. The broker decides *whether* and *how* it runs. Tool servers do *the work*. Three roles, never collapsed.

The previous `agent-broker/` implementation collapsed the broker and tool-server roles into one Python process and silently broke this principle. The greenfield rebuild keeps them in separate processes (and separate Docker containers).

## 3. No standing authority on the agent host

The agent holds a broker bearer token bound to one caller + profile. That token does not directly perform actions; it grants the right to *ask*. Stealing it should not enable a destructive operation without additional gates (policy review, human approval).

The agent should never hold SaaS tokens, cloud credentials, database passwords, SSH keys, or 1Password Connect tokens. Local-side credentials should be a low-power broker token at most.

## 4. Secrets live with the workload

Each tool's secrets are resolved at container-start time by toolyardd and injected into container tmpfs as per-tool files at `/run/secrets/<name>`. Tool containers do not hold 1Password Connect tokens; they read files and use `/run/toolyard/secrets.sock` only for descriptor-allowlisted writable fields. The toolyard is the trust boundary that enforces per-tool scoping within a single shared `ToolServer` vault, which keeps the topology operationally tractable as the number of tools grows.

The broker is not in the secret path. It cannot read upstream credentials. It does not inject auth into tool requests.

This is a refinement of the essay's "don't trust tools with secrets" rule. The original rule was about local-sphere tools the agent could introspect. Tool servers behind a broker boundary are not in that sphere — the agent cannot reach them, fork their processes, or read their env. See [ADR 004](decisions/004-secrets-at-workload.md) for full context.

## 5. Fail closed

Unknown caller, expired token, no matching policy rule, timed-out approval, network failure to a tool server — all default to deny. The system is unhelpful before it is unsafe.

Approval pending past the timeout transitions to `expired` and cannot be retroactively approved. Token revocation is one operator command and takes effect immediately; in-flight requests using a revoked token fail at the next step.

## 6. Every action is auditable and revocable

The broker records: caller, profile, requested operation, arguments (with secrets stripped), policy decision, approver (if any), dispatch result, and timing. Every record gets an immutable `id`.

Audit must be able to answer four questions for any incident:
- What did the agent ask for?
- What was decided, and by whom?
- What actually ran?
- Which credentials made it possible?

Token revocation kills future requests from that token. Operators do not have to find and stop in-flight requests separately.

## 7. Approval describes the operation, not the command

The Discord approval card does not say "Approve `curl`?" It says: which agent, which tool, which operation, which target, what data class, what would change, and what the policy decision was.

Agents are good at making routine operations look unremarkable. The approval surface must show the operation, not the wrapper.

## 8. Easy to onboard a tool, hard to bypass a control

Tool onboarding is "drop a folder with a `toolyard.yaml`, pick an entry point, run `toolyard up`." That's it. Classification, risk analysis, capability inventory, and policy decisions live in *separate* layers and never block adding a tool.

But none of that ergonomic ease relaxes the controls. A new tool added to the toolyard is not automatically reachable by an agent — the agent's profile has to explicitly include it, or it stays denied.

The principle: friction on adding *tools* should be near-zero. Friction on adding *authority* should be deliberate and visible.
