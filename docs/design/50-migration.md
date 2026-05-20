# Rebuild from `agent-broker/`

What was lifted, dropped, and deferred when rebuilding the system into the four-component shape ([`01-architecture.md`](01-architecture.md)).

The old `agent-broker/` code is no longer in this repo; this doc is the historical record of the file-by-file disposition during the rebuild.

The original `agent-broker/` was a 4,144-LOC Python monolith with all M0–M7 milestones marked done. The architecture was upside-down (broker absorbed the tool-server tier), so we rebuilt from scratch.

**The old broker was never in production** — nothing depended on it. The rebuild was a clean cutover: build the new system, validate it works, delete the old.

We keep the schema, the vocabulary, the descriptor concept, and the standalone `op-connect-shim`. Everything else is rewritten.

## File-by-file decisions for `agent_broker/`

| File | LOC | Decision | Notes |
|---|---|---|---|
| `api.py` | 567 | **Rewrite** | New HTTP surface per [`10-broker.md`](10-broker.md). Drop the embedded HTML approval page entirely. |
| `service.py` | 330 | **Rewrite** | Split into request handler, policy decision call, dispatcher. Forwarding logic shrinks to <100 LOC since execution moves out. |
| `db.py` | 761 | **Lift schema, rewrite code** | Same table shapes (callers, tokens, action_requests, approvals, grants, audit_events). Clean up the query code; drop migration logic. |
| `dispatcher.py` | 114 | **Drop** | REST execution moves into tool containers. Broker has a small HTTP client for forwarding only. |
| `mcp_client.py` | 296 | **Drop** | Blind JSON-RPC forwarding (~50 LOC) replaces it. See [ADR 002](decisions/002-blind-jsonrpc-routing.md). |
| `jobs.py` | 104 | **Drop** | `sandbox-job` deferred. Rebuild only if needed. |
| `policy.py` | 248 | **Replace** | Simple YAML ACL, ~100 LOC. See [ADR 005](decisions/005-policy-simple-now.md). |
| `catalog.py` | 77 | **Drop** | No discovery-generated catalog. Descriptors (`toolyard.yaml`) are source of truth. |
| `discovery.py` | 369 | **Drop** | Same as catalog. |
| `config.py` | 66 | **Replace** | Simpler tool-config loader that reads `tools/<id>/toolyard.yaml`. |
| `classify.py` | 167 | **Drop** | Heuristic risk classification. Risk class declared explicitly in `toolyard.yaml` `operations[].risk`. |
| `secrets.py` | 155 | **Drop** | Broker is not in secret path. See [ADR 004](decisions/004-secrets-at-workload.md). |
| `notifications.py` | 82 | **Drop** | Discord approver bot replaces ntfy. |
| `cli.py` | 445 | **Rewrite** | Subset of commands; see `10-broker.md`. Drop `propose`, `review`, `decide`, `sync`, `discover`. |
| `models.py` | 96 | **Keep concepts, rewrite** | Reuse `Capability`, `PolicyDecision`, `PolicyInput` shapes (rename slightly). |
| `maintenance.py` | 179 | **Drop** | Agent-assisted maintenance (M7) deferred — unproven. |
| `review.py` | 57 | **Drop** | Operator review surface, low value. |
| `connectors/` | — | **Drop** | Connectors (e.g., Calendar Demo) become standalone tool containers in `tools/<id>/`. |
| `paths.py` | 28 | **Replace** | Trivial; superseded by env-var config. |

**Total kept (in any form): ~850 LOC of concepts/schema. Total dropped: ~3,300 LOC.**

## Tool descriptors

Existing: `agent-broker/tools/<tool>/tool.yaml`.

| Existing tool | Decision |
|---|---|
| `media` | Rebuild as a Docker-based REST tool. Existing route fragments map cleanly to FastAPI routes. The Media auth credentials move into the `ToolServer` vault as item `media`. |
| `tasks` | Rebuild as MCP-HTTP tool. Currently routes through a local MCP proxy at `:4501`; that proxy itself becomes the toolyard container. |
| `calendar` | Rebuild as a REST tool. The Apple iCloud CalDAV connector code moves into the container; iCloud app-password moves into `ToolServer/calendar`. |
| `broker-jobs` | **Drop**. Sandbox-job execution is deferred. |
| `win-re-tools` | **Drop** (already disabled). |

Descriptor format changes:

| Old `tool.yaml` field | New `toolyard.yaml` field | Notes |
|---|---|---|
| `id` | `id` | same |
| `backend.type: rest` | `type: rest` | |
| `backend.type: mcp-stdio` | `type: mcp-stdio` | toolyard provides stdio→http adapter |
| `backend.type: mcp-remote` | `type: mcp-http` | broker no longer reaches off-host; remote tools run as local containers fronting remote APIs |
| `backend.type: sandbox-job` | — | dropped |
| `backend.base_url` | — | no longer relevant; tool is at `127.0.0.1:<port>` |
| `backend.command` (mcp-stdio) | inside the Dockerfile | |
| `discovery.type` + `routes`/`spec`/`operations` | `operations` (declarative metadata) | broker forwards blind |
| `secrets` (old: logical Connect refs) | `secrets` (new: vault/item/field; see [`40-secrets.md`](40-secrets.md)) | resolved by toolyard, not by broker or tool |
| — | `entrypoint.{build,image,port}` | new (Docker-specific) |
| — | `volumes`, `network`, `healthcheck`, `risk_class_default` | new (Docker-specific) |

## 1Password vault topology

The old `agent-broker/` referenced per-service vaults (e.g., `Remote Tools`, `Apple Family`). The new design uses a single shared `ToolServer` vault with one item per tool. See [`40-secrets.md`](40-secrets.md).

If existing 1Password vaults already hold credentials that the new tools will need:

- Easiest: copy the relevant fields into the new `ToolServer` vault, one item per tool. Old vaults can be archived.
- Alternative: override `vault:` and `item:` in `toolyard.yaml` for tools whose secrets you don't want to move. This works but defeats the operational simplicity of the shared-vault model.

A new Connect token scoped to the `ToolServer` vault (read-only) is required for the toolyard. Existing per-tool Connect tokens can be revoked once their tools are rebuilt.

## Policies

| Existing | Decision |
|---|---|
| `policies/baseline.rego` | **Drop** (OPA deferred) |
| `policies/baseline_test.rego` | **Drop** |
| `policies/profiles.yaml` | **Split** into `policies/profiles/<profile>.yaml`, one per profile, simpler schema |
| `policies/overrides.yaml` | **Drop** (no catalog to override; risk declared in `toolyard.yaml`) |

New policy file layout:

```
policies/
└── profiles/
    ├── home-default.yaml
    ├── readonly.yaml
    ├── approver.yaml            # for the Discord bot
    └── ...
```

## State

The old `state/broker.sqlite3` has no production data — delete or archive it alongside the rest. Greenfield gets a new database.

## Deploy

Reusable shape:
- Systemd unit shape for the broker (binds 127.0.0.1, scoped ReadWritePaths). Adapt per [`10-broker.md`](10-broker.md) config.
- Tailscale Serve route under `broker.<tailnet>.ts.net`, proxying to the broker's localhost bind address.

New units needed:
- Systemd unit for the toolyard (or just a one-shot `toolyard up` invocation at boot).
- Systemd unit for the Discord approver bot (or run it as a toolyard-managed container — see [`30-approver-discord.md`](30-approver-discord.md)).

## `op-connect-shim`

**No changes.** Stays as-is in `op-connect-shim/`. Used by the toolyard for secret resolution. Optionally used inside containers that opt into `secrets_provider: container` (rare).

## Order of work

A suggested sequence that lets you validate each layer before depending on it:

1. **Broker, no forwarding.** Token issuance, bearer auth, profile ACL, action_request lifecycle (synthetic — pretend dispatch always succeeds), audit. End-to-end testable via `brokerctl` + curl, no tools required.
2. **Toolyard, one tool, no broker.** Write a fresh `hello-rest` from the template. Get it running as a Docker container reachable at `127.0.0.1:<port>`. Verify per-tool secrets resolution end-to-end.
3. **Broker reads registry.** Broker reads `tools/<id>/toolyard.yaml`, exposes `/v1/registry`. No forwarding yet.
4. **Broker forwards HTTP.** Broker can forward `POST /v1/actions/<tool>.<op>` to the tool. End-to-end agent→broker→tool works for a single REST tool.
5. **MCP blind-routing.** Broker forwards `POST /mcp/<tool>` JSON-RPC frames. Test with a fresh `time-mcp` tool from the template.
6. **Discord bot.** Build the bot. Set up a private channel. Wire it to the broker. Validate the approval lifecycle end-to-end with one approval-required op.
7. **Build one existing tool's replacement.** Most likely Media (well-understood, varied risk classes). Build it as a Docker-based tool in the new toolyard. Wire a test broker token through it end-to-end.
8. **Build remaining tools.** Tasks, Calendar. Drop `broker-jobs` and `win-re-tools` unless someone explicitly asks for them.
9. **Archive the old `agent-broker/`.** At any point — nothing in production depends on it. Rename to `agent-broker-archive/` for reference, or delete outright.

Each step is independently validatable. Each step can be paused, reviewed, or reversed without unwinding the others.

## What this rebuild does not solve

Worth being honest about:

- **Performance**: a fresh Python broker is not faster than the old one. Same magnitude.
- **Multi-host scaling**: still single-host. Designed to be split later if needed.
- **Observability**: still SQLite audit. Dashboards remain future work.
- **Per-tool granular permissions**: profile is the granularity at v1. Per-tool-per-op ACLs are expressible via the YAML, but not richer than that.

The rebuild solves: architectural drift, monolithic deployment, secrets-in-the-broker, embedded HTML in `api.py`, dual-policy-engine maintenance, tool onboarding friction, per-tool Connect-token sprawl, and a long tail of half-finished features (M7 propose/maintain, sandbox-jobs, connectors-as-Python-modules).

It's the smaller, cleaner, more honest version of the same system.
