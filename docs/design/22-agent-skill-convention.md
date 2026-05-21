# Agent Skill Convention

This convention describes the client-side half of Toolstack integrations:
small agent skills that call broker actions while the real implementation runs
on upstream Toolstack infrastructure.

Use this pattern when a tool has many operations, nontrivial auth, long-running
service code, or secrets that should not live in the skill bundle. The skill is
a thin, portable client. The broker and tool service remain the authority and
execution boundary.

## Goals

- Keep agent context small through progressive disclosure.
- Keep local setup caller-scoped and portable across local agent hosts.
- Avoid local dependency drift, virtual environments, or startup-heavy protocol
  clients for routine actions.
- Keep downstream credentials, secret-manager tokens, and service logic out of
  skill directories.
- Make each skill easy to copy for the next tool without copying deployment
  details.

## Standard Skill Shape

```text
<tool>-ctrl/
├── SKILL.md
├── scripts/
│   ├── <tool>-cli
│   ├── <tool>-cli.mjs
│   ├── bootstrap.mjs
│   └── toolstack-skill.mjs
└── references/
    ├── operations.md
    └── workflows.md
```

`SKILL.md` should stay short. It tells the agent when to use the skill, how to
run the CLI, which operations are safe or review-gated, and which reference file
to read for task-specific details.

`scripts/<tool>-cli` is the stable executable entry point. It should work from
any current directory and should not require `bash -c`, a manual `cd`, package
installation, or caller-specific paths.

`scripts/<tool>-cli.mjs` is the default implementation for thin broker clients.
Use dependency-free Node so the skill does not need a venv or install step.
Other runtimes are acceptable only if they preserve the same command and config
contract.

`scripts/bootstrap.mjs` creates local or global Toolstack caller config and
token directories. It does not fetch or mint tokens by itself.

`scripts/toolstack-skill.mjs` may hold shared helper logic for config loading,
token-file handling, broker calls, rendering, and local caller config
discovery.

`references/` files hold detailed usage guidance that the agent should load only
when needed.

## Configuration Contract

Do not hardcode a deployment URL or broker token in skill code.

Every Toolstack-backed skill declares a broker namespace and default caller:

```js
loadToolstackConfig({
  toolstackName: "<broker-tool>",
  defaultCaller: "<caller>",
  envPrefix: "<TOOL>",
});
```

Use broker tool names, not skill directory names, for Toolstack namespaces. This
lets multiple skills share one broker tool while using different callers.

Runtime precedence:

1. Environment variables supplied by the caller or process.
2. Local Toolstack caller config.
3. Global XDG Toolstack caller config.
4. A clear setup error that names the checked config files.

Use uppercase, tool-scoped environment overrides:

```bash
<TOOL>_TOOLSTACK_CALLER=<caller>
<TOOL>_TOOLSTACK_URL=<toolstack-url>
<TOOL>_TOOLSTACK_TOKEN=<raw broker token>
<TOOL>_TOOLSTACK_TOKEN_FILE=<token-file>
```

The token file is the normal path. The raw token env var is a fallback for
systems that already manage secrets in the process environment.

Local config should be discovered from the active caller or installed
skill path. Global fallback uses `${XDG_CONFIG_HOME:-$HOME/.config}`. Both use
the same Toolstack layout:

```text
<config-home>/toolstack/<broker-tool>/callers/<caller>.env
<config-home>/toolstack/<broker-tool>/tokens/<caller>.token
```

Caller `.env` files use generic keys, not tool-prefixed keys:

```bash
TOOLSTACK_URL=<toolstack-url>
TOOLSTACK_TOKEN_FILE=tokens/<caller>.token
```

Relative `TOOLSTACK_TOKEN_FILE` paths resolve from
`<config-home>/toolstack/<broker-tool>/`. This keeps each broker tool's caller
config and token files together.

## Broker Call Contract

The CLI calls broker action endpoints directly:

```text
POST <TOOLSTACK_URL>/v1/actions/<tool>.<operation>
Authorization: Bearer <broker-token>
Content-Type: application/json

{
  "arguments": { ... },
  "reason": "<skill-name> <command>"
}
```

The skill should render common results for the agent, but the broker/tool
response shape remains the source of truth. If the broker returns a pending
review response, the CLI should show that clearly rather than retrying or trying
to bypass review.

## What Does Not Belong In A Skill

- Downstream SaaS tokens, database credentials, cloud credentials, SSH keys, or
  secret-manager tokens.
- Service implementation code that belongs in a Toolstack tool container.
- Browser auth flows, local credential stores, or token-refresh persistence for
  upstream services.
- Broad MCP discovery clients unless MCP is the intended user-facing interface.
- Hardcoded deployment URLs, operator-specific paths, or VM-specific token
  locations.
- Normal-use commands that depend on `bash -c`, shell profile state, a working
  directory, package installation, or a language-specific virtual environment.

## Minimal Bootstrap Behavior

Bootstrap should be deterministic and safe to rerun:

```bash
node "${SKILL_DIR}/scripts/bootstrap.mjs" --url <toolstack-url>
```

For alternate callers or global defaults:

```bash
node "${SKILL_DIR}/scripts/bootstrap.mjs" --caller <caller> --url <toolstack-url>
node "${SKILL_DIR}/scripts/bootstrap.mjs" --global --caller <caller> --url <toolstack-url>
```

For read-only variants, prefer a flag that changes only the selected Toolstack
caller:

```bash
node "${SKILL_DIR}/scripts/bootstrap.mjs" --readonly --url <toolstack-url>
```

Bootstrap should:

- create `<config-home>/toolstack/<broker-tool>/callers/<caller>.env`;
- create `<config-home>/toolstack/<broker-tool>/tokens/`;
- write `TOOLSTACK_URL` and `TOOLSTACK_TOKEN_FILE`;
- keep file permissions private where the filesystem supports it;
- avoid embedding a raw token unless explicitly provided by the operator.

## Relationship To Tool Templates

Server-side tools follow the template in
[`21-tool-template.md`](21-tool-template.md). This convention is the matching
agent-side wrapper. A complete integration usually has both:

- a Toolstack tool service with a `toolyard.yaml`, caller policy, and broker
  operations;
- a minimal `<tool>-ctrl` skill that exposes the right operations to the agent
  only when the skill is invoked.

The skill should not widen authority. It should only make a scoped broker token
easy for the agent to use.
