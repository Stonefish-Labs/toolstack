# Toolstack

Toolstack is a local-first tool execution stack for agents. It keeps agent
credentials narrow by routing actions through a broker, running tools in
managed containers, and asking a human approver for operations that need review.

## Components

- `broker/` - the authority boundary. It authenticates callers, evaluates
  profile policy, dispatches approved actions, and writes audit events.
- `toolyard/` - the Docker lifecycle runner and per-tool secret boundary. It
  reads `tools/<id>/toolyard.yaml`, starts enabled tools, and injects resolved
  secrets into container tmpfs.
- `discord-approver/` - a Discord bot that mirrors the broker approval queue
  into a human review channel.
- `tools/` - example and working tool containers, including REST and MCP-style
  services.
- `docs/` - design notes, deployment examples, and operator guides.
- `lib/op-connect-shim/` - a small dependency-free helper for 1Password Connect.

## Local Development

Each Python service is currently developed as its own package:

```bash
cd broker
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -q
```

Repeat the same pattern in `toolyard/` and `discord-approver/`.

Some tool tests require their own dependencies from `tools/<tool>/requirements.txt`.
Docker-backed tests may also require a local Docker daemon.

## Configuration

Real tokens, `.env` files, SQLite state, audit logs, virtualenvs, and generated
build artifacts are intentionally ignored. Use the example files under
`docs/deployment/env/` and `lib/op-connect-shim/*.env.example` as templates.

Deployment examples use placeholder hostnames such as
`https://broker.your-tailnet.ts.net` and placeholder token file paths. Replace
them with values for your own environment outside the repository.

## Documentation

Start with:

- `docs/design/01-architecture.md` for the system shape
- `docs/deployment/README.md` for the VM deployment walkthrough
- `docs/user-guide.md` for day-to-day operator and agent usage
- `docs/trust-agents-with-action-not-access.md` for the project thesis
- `docs/history/` for archived implementation plans
