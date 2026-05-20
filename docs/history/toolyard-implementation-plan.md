# Historical Implementation Plan: Toolyard

This document is archived implementation history. It describes an earlier
toolyard build slice and is not the current project status or source of truth.
For current behavior, see the component README and the active design docs under
`../design/`.

**Audience**: this is a hand-off plan for an implementer (likely another Claude model) who has not seen the prior conversation. Read the referenced design docs first — they have the architectural context.

## What you're building

The toolyard is the tool-server lifecycle component of the toolserver system. It reads `tools/<id>/toolyard.yaml` files, resolves each tool's secrets from a shared 1Password `Tools` vault at container-start time, and runs each tool as a Docker container bound to `127.0.0.1:<port>`. It exposes a small CLI for operators: `up`, `down`, `restart`, `add`, `ls`, `logs`, `validate`, `secrets`.

This slice is **deliberately isolated from the broker**. At the end of it, you'll be able to drop a folder into `tools/`, run one command, and have a sandboxed REST tool reachable via direct `curl http://127.0.0.1:<port>/...`. The bot and broker continue to use synthetic dispatch — the broker can't reach these tools yet. That comes in the next slice, which adds `HTTPDispatcher` / `MCPDispatcher` and registry-reading to the broker.

What this slice gives you when complete:
- A `toolyard` CLI that manages tool containers.
- A `hello-rest` tool that proves the template works end-to-end (toolyard.yaml → Docker container → mounted secrets → live HTTP endpoint).
- Per-tool secrets resolution from the shared `Tools` vault, written into the container as files (no Connect token inside the tool container by default).
- A `DockerDriver` and `SecretResolver` seam so the toolyard's logic is testable without Docker or 1Password Connect.

## Required reading

Before writing code, read these in order:

1. `../trust-agents-with-action-not-access.md` — the system's thesis (skim).
2. `../design/00-principles.md` — operational principles. Pay attention to principle 4 (secrets live with the workload) and principle 8 (easy to onboard a tool).
3. `../design/01-architecture.md` — where the toolyard fits in the four-component shape.
4. `../design/20-toolyard.md` — **the spec for this component.** Read fully. The `toolyard.yaml` schema, lifecycle behavior, container conventions, and configuration table are all here.
5. `../design/21-tool-template.md` — **the spec for the hello-rest tool you'll build.** Use the REST example as your template.
6. `../design/40-secrets.md` — **the spec for per-tool secrets resolution.** Critical: read the "How the toolyard resolves" section carefully.
7. `../design/decisions/003-docker-sandboxing.md` — why Docker, what's deliberately deferred.
8. `../design/decisions/004-secrets-at-workload.md` — the trust model for secret handling.
9. `../../lib/op-connect-shim/README.md` — the library you'll use to talk to 1Password Connect.
10. `broker-implementation-plan.md` — for context on what the broker will eventually consume. You don't change the broker in this slice, but the next slice will, and the toolyard's `toolyard.yaml` files must be readable by both components.

If anything in this plan conflicts with the design docs, the design docs win — flag the conflict and ask before deviating.

## Goal

A working toolyard that:

1. Reads `tools/<id>/toolyard.yaml` files and validates the schema.
2. For each tool's declared secrets, fetches the field from 1Password Connect using its own scoped Connect token, then writes the value to `<TOOLYARD_SECRETS_DIR>/<tool>/<name>` on the host (mode 0600, owned by the toolyard user).
3. If any declared secret has `writable: true`, also copies a write-capable Connect token into the per-tool secrets directory.
4. Builds or pulls Docker images per tool and runs each container with:
   - `127.0.0.1:<port>` host port binding only.
   - The per-tool secrets dir mounted at `/run/secrets` read-only.
   - Non-root user, `--cap-drop=ALL`, read-only root filesystem where the tool tolerates it.
   - `OP_CONNECT_HOST` / `OP_CONNECT_TOKEN_FILE` env set only for tools with writable fields.
5. Probes healthcheck endpoints if declared and reports status.
6. Provides operator commands: `up`, `down`, `restart`, `add`, `ls`, `logs`, `validate`, `secrets`.
7. Ships with one working example tool (`tools/hello-rest/`) that demonstrates the full lifecycle end-to-end.

## Loose-coupling requirements

To keep this swappable and testable:

1. **`DockerDriver` protocol** — the toolyard never calls `docker` directly. All container operations go through `docker_driver.py`. Implementations: `CLIDockerDriver` (shells out to `docker`) and `MockDockerDriver` (in-memory, for tests).
2. **`SecretResolver` protocol** — the toolyard never imports `op_connect_shim` directly outside `secrets.py`. Implementations: `ConnectSecretResolver` (real, uses `op-connect-shim`) and `MockSecretResolver` (in-memory, for tests).
3. **Schema validation is a separate concern** — `schema.py` validates `toolyard.yaml` content using Pydantic (or equivalent). Other modules consume validated `ToolDescriptor` objects, never raw YAML.
4. **`registry.py` is the filesystem reader** — walks `tools/` and produces validated descriptors. Other modules don't read YAML files directly.
5. **CLI is a thin wrapper** — `cli.py` does argument parsing and dependency wiring. The actual lifecycle logic lives in `lifecycle.py`, which takes the driver, resolver, and registry as injected dependencies.

The test for these seams: `lifecycle.py`, `secrets.py`, `schema.py`, `registry.py` should be testable without Docker installed and without 1Password Connect running.

## Project layout

```
toolyard/
├── README.md                         # write this as part of step 1
├── pyproject.toml                    # or requirements.txt — pick one
├── .gitignore                        # standard Python ignores + state/
├── src/toolyard/
│   ├── __init__.py
│   ├── config.py                     # env vars + validation
│   ├── models.py                     # ToolDescriptor, SecretRef, etc.
│   ├── schema.py                     # toolyard.yaml validation
│   ├── secrets.py                    # SecretResolver protocol + ConnectSecretResolver
│   ├── docker_driver.py              # DockerDriver protocol + CLIDockerDriver
│   ├── registry.py                   # walk tools/ and return descriptors
│   ├── lifecycle.py                  # up/down/restart/add logic (the real work)
│   ├── healthcheck.py                # HTTP probe + status tracking
│   └── cli.py                        # argparse + entry point
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # fixtures: temp tools dir, mock driver, mock resolver
│   ├── fixtures/                     # sample toolyard.yaml files for tests
│   │   ├── valid/
│   │   └── invalid/
│   ├── test_schema.py                # schema validation: valid + invalid examples
│   ├── test_secrets.py               # resolution against mock resolver
│   ├── test_registry.py              # walk a fixture dir, get descriptors
│   ├── test_lifecycle.py             # up/down/restart with mock driver + mock resolver
│   ├── test_docker_driver.py         # smoke test the CLI driver (skipped without docker)
│   └── test_cli.py                   # end-to-end via subprocess against mocks
└── docs/
    └── manual-testing.md             # bring-up steps + hello-rest verification
```

And outside the toolyard project, the example tool:

```
tools/
└── hello-rest/
    ├── toolyard.yaml
    ├── Dockerfile
    ├── app.py
    └── requirements.txt
```

State directory (created at runtime, not in the repo):

```
state/
└── runtime.json                      # optional cache of container_id, image_digest, last_started_at
```

Per-tool secrets directory (default `/var/lib/toolyard/secrets` per [`../design/20-toolyard.md`](../design/20-toolyard.md)). Created at runtime, mode 0700, owned by toolyard user.

## Module-by-module spec

### `config.py`

Load env vars, fail fast on missing required values.

| Env var | Required | Default | Notes |
|---|---|---|---|
| `TOOLYARD_OP_CONNECT_HOST` | yes | — | URL of the 1Password Connect service, e.g. `http://192.168.1.5:19080` |
| `TOOLYARD_OP_CONNECT_TOKEN_FILE` | yes | — | Read-only Connect token file (scoped to `Tools` vault) |
| `TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE` | no | unset | Write-capable Connect token file. Required only if any tool declares `writable: true` secrets |
| `TOOLYARD_TOOLS_DIR` | no | `./tools` | Where `<id>/toolyard.yaml` files live |
| `TOOLYARD_SECRETS_DIR` | no | `/var/lib/toolyard/secrets` | Where per-tool resolved secrets are written |
| `TOOLYARD_STATE_DIR` | no | `./state` | Where `runtime.json` (optional cache) lives |
| `TOOLYARD_USER_UID` | no | `10000` | UID for the non-root user inside containers |
| `TOOLYARD_BROKER_RELOAD_URL` | no | unset | Optional broker reload URL (not used in this slice) |

### `models.py`

Pydantic models (recommended) or dataclasses. Pick one and stay consistent with the rest of the project.

```python
class SecretRef(BaseModel):
    name: str                          # file name in /run/secrets/<name>
    vault: str = "Tools"               # default
    item: str | None = None            # default = tool's id (resolved at validation time)
    field: str
    writable: bool = False

class HealthcheckSpec(BaseModel):
    http: str                          # path on container, e.g. "/health"
    interval_seconds: int = 30
    start_period_seconds: int = 10

class VolumeSpec(BaseModel):
    host: str                          # host path
    container: str                     # container path
    mode: Literal["ro", "rw"] = "ro"

class EntrypointSpec(BaseModel):
    build: str | None = None           # path to Dockerfile context, e.g., "."
    image: str | None = None           # OR a pre-built image ref
    port: int                          # container port
    command: list[str] = []

    # Exactly one of {build, image} must be set.

class OperationSpec(BaseModel):
    op: str
    risk: Literal["read", "write", "destructive"] = "write"
    redact_args: list[str] = []

class ToolDescriptor(BaseModel):
    id: str                            # routing key; pattern: [a-z][a-z0-9-]*
    type: Literal["rest", "mcp-http", "mcp-stdio"]
    description: str = ""
    enabled: bool = True
    entrypoint: EntrypointSpec
    secrets: list[SecretRef] = []
    env: dict[str, str] = {}
    volumes: list[VolumeSpec] = []
    network: Literal["default", "isolated", "host"] = "default"
    healthcheck: HealthcheckSpec | None = None
    risk_class_default: Literal["read", "write", "destructive"] = "write"
    operations: list[OperationSpec] = []
```

After validation, fill in `SecretRef.item` defaults to the tool's `id` if unset.

### `schema.py`

Loads `toolyard.yaml`, validates, returns `ToolDescriptor` or raises a clear validation error.

```python
def load_descriptor(path: Path) -> ToolDescriptor:
    """Parse YAML, validate against the schema, return descriptor."""

def validate_descriptor_dict(data: dict) -> ToolDescriptor:
    """Lower-level: validate an already-parsed dict."""
```

Custom validations (beyond Pydantic basics):
- Exactly one of `entrypoint.build` or `entrypoint.image` must be set (not both, not neither).
- `id` matches `^[a-z][a-z0-9-]*$` (lowercase alphanumeric + dashes, starts with a letter).
- `secrets[].name` matches `^[a-z][a-z0-9_-]*$` (no path traversal).
- `secrets[].name` is not `_connect_token` (reserved for the writable-fields write token).
- If any `secrets[].writable: true` is present, but `TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE` is unset → schema validation should pass (declarations are static), but `up` should fail with a clear message.

Tests against fixtures in `tests/fixtures/valid/` and `tests/fixtures/invalid/`. Each invalid fixture has a short comment explaining what's wrong.

### `secrets.py`

```python
class SecretResolver(Protocol):
    def resolve(self, vault: str, item: str, field: str) -> str: ...

class ConnectSecretResolver:
    """Wraps op-connect-shim's OnePasswordConnect."""
    def __init__(self, host: str, token_file: str): ...
    def resolve(self, vault, item, field) -> str:
        return self._op.get_field(vault=vault, item=item, field=field)

class MockSecretResolver:
    def __init__(self, values: dict[tuple[str, str, str], str]): ...
    def resolve(self, vault, item, field):
        return self.values[(vault, item, field)]
```

Plus the per-tool resolution + write function:

```python
def resolve_and_write_secrets(
    *,
    descriptor: ToolDescriptor,
    resolver: SecretResolver,
    secrets_dir: Path,
    write_token_file: Path | None,
) -> None:
    """
    For each secret in descriptor.secrets:
      1. Call resolver.resolve(vault, item, field) -> value.
      2. Write value to <secrets_dir>/<tool_id>/<name>, mode 0600.
    If any secret has writable=true:
      3. Copy write_token_file -> <secrets_dir>/<tool_id>/_connect_token, mode 0600.
    Ensure the per-tool dir is created with mode 0700.
    Never log or store the values anywhere else.
    """
```

Don't keep resolved values in memory after writing. Pass through, write, forget.

Use `op-connect-shim` from `../../lib/op-connect-shim/op_connect_shim.py`. Import it via path manipulation or by symlinking it into your project — your call (the shim is stdlib-only, so vendoring works).

### `docker_driver.py`

```python
class DockerDriver(Protocol):
    def build(self, *, context: Path, tag: str) -> str:
        """Build image, return image_id."""
    def pull(self, image: str) -> str:
        """Pull image, return image_id."""
    def run(
        self, *,
        name: str,
        image: str,
        port_mapping: tuple[int, int],   # (host_port, container_port)
        bind_addr: str = "127.0.0.1",
        volumes: list[tuple[str, str, str]],  # [(host, container, mode), ...]
        env: dict[str, str],
        user: str = "10000:10000",
        cap_drop_all: bool = True,
        read_only: bool = False,
        command: list[str] | None = None,
    ) -> str:
        """Run container detached, return container_id."""
    def stop(self, name: str) -> None: ...
    def remove(self, name: str) -> None: ...
    def logs(self, name: str, tail: int | None = None) -> str: ...
    def inspect(self, name: str) -> dict: ...
    def ps(self, name_prefix: str = "toolyard-") -> list[dict]: ...

class CLIDockerDriver(DockerDriver):
    """Shells out to the `docker` CLI via subprocess."""

class MockDockerDriver(DockerDriver):
    """In-memory state. For tests."""
```

The `CLIDockerDriver` uses `subprocess.run` against the `docker` binary. Use `--name toolyard-<id>` consistently so containers are findable.

For the slice: implementing `docker compose` is **not** required. One container per tool, straightforward `docker run`.

### `registry.py`

```python
def walk_tools(tools_dir: Path) -> Iterator[ToolDescriptor]:
    """Yield validated descriptors for each <id>/toolyard.yaml found."""

def get_descriptor(tools_dir: Path, tool_id: str) -> ToolDescriptor | None: ...

def reload_index(tools_dir: Path) -> dict[str, ToolDescriptor]:
    """Return a fresh id -> descriptor map."""
```

Tests: a fixture tools/ directory with 2-3 valid and 1 invalid descriptor. Verify `walk_tools` yields the valid ones and surfaces errors clearly for the invalid one.

### `lifecycle.py`

The real work. Pure functions that take dependencies and return outcomes — easy to test with mocks.

```python
@dataclass
class UpResult:
    tool_id: str
    container_id: str
    image_id: str
    host_port: int
    healthy: bool | None         # None if no healthcheck declared

def up(
    *,
    descriptor: ToolDescriptor,
    config: Config,
    driver: DockerDriver,
    resolver: SecretResolver,
) -> UpResult:
    """
    1. Validate descriptor isn't disabled.
    2. Build or pull image (driver.build / driver.pull).
    3. Resolve secrets and write to <secrets_dir>/<tool_id>/ (secrets.resolve_and_write_secrets).
    4. Compose docker run args:
       - --name toolyard-<id>
       - -p 127.0.0.1:<port>:<port>
       - -v <secrets_dir>/<id>:/run/secrets:ro
       - --user <UID>:<UID>
       - --cap-drop=ALL
       - --read-only if declared
       - If any writable secret: -e OP_CONNECT_HOST=<host>, -e OP_CONNECT_TOKEN_FILE=/run/secrets/_connect_token
       - User env from descriptor
       - User volumes from descriptor
    5. driver.run(...) -> container_id.
    6. If healthcheck declared: probe (call healthcheck.wait_for_healthy).
    7. Return UpResult.
    """

def down(*, tool_id: str, driver: DockerDriver) -> None: ...
def restart(*, tool_id: str, config, driver, resolver) -> UpResult: ...
def add(*, source_folder: Path, config) -> ToolDescriptor: ...
def list_tools(*, config, driver: DockerDriver) -> list[ToolStatus]: ...
```

`ToolStatus` is a snapshot for `toolyard ls`: id, enabled, container present, healthy, host_port, image_digest.

The `add` command: validates the toolyard.yaml in the source folder, then symlinks (or copies) the folder into `TOOLYARD_TOOLS_DIR`. It does NOT auto-start (operator runs `toolyard up <id>` separately).

### `healthcheck.py`

```python
def wait_for_healthy(
    *,
    host_port: int,
    spec: HealthcheckSpec,
    timeout_seconds: int | None = None,
) -> bool:
    """Probe http://127.0.0.1:<port><path> until 2xx or start_period_seconds exceeded."""
```

Use `httpx` (sync is fine here). Sleep `interval_seconds` between probes. Return `True` on first 2xx, `False` if start_period_seconds expires.

The toolyard logs the result. The container is left running either way (operator decides whether to investigate or restart) — this is per [`../design/20-toolyard.md`](../design/20-toolyard.md).

### `cli.py`

`toolyard` subcommands:

```
toolyard up [<id>]                  # start one or all enabled
toolyard down [<id>]                # stop one or all
toolyard restart <id>               # rebuild/pull, re-resolve secrets, restart
toolyard add <folder>               # adopt a folder containing a toolyard.yaml
toolyard logs <id> [--follow]       # passthrough to docker logs
toolyard ls [--json]                # show registry + container status
toolyard validate <folder>          # validate a toolyard.yaml without running
toolyard secrets <id>               # show which 1Password refs this tool needs (no values)
```

Use `argparse` (stdlib) or `click`. Either works.

Important UX details:
- `validate` exits 0 on valid, non-zero on invalid, with a clear error message.
- `secrets` prints something like:
  ```
  hello-rest declares 1 secret:
    api_key  ->  Tools/hello-rest/API_KEY  (read)
  ```
  with no values, ever.
- `up` prints a clean line per tool: `hello-rest: building... done. starting... ok. healthy.`
- `ls --json` is consumed by future tooling; keep the shape stable: `{"tools": [{"id": ..., "enabled": ..., "running": ..., "healthy": ..., "host_port": ..., ...}]}`

## The `hello-rest` tool

Build this in `../../tools/hello-rest/`. Use [`../design/21-tool-template.md`](../design/21-tool-template.md) as the canonical reference — copy the REST example verbatim except:

- Use Python 3.12 as the base image.
- Verify `op-connect-shim` does NOT appear in `requirements.txt`. The tool reads `/run/secrets/api_key` directly:
  ```python
  def secret(name: str) -> str:
      with open(f"/run/secrets/{name}") as f:
          return f.read().strip()

  api_key = secret("api_key")
  ```

`toolyard.yaml`:

```yaml
id: hello-rest
type: rest
description: "Hello-world REST tool"
enabled: true

entrypoint:
  build: .
  port: 5000

secrets:
  - name: api_key
    field: API_KEY                    # vault=Tools (default), item=hello-rest (default = id)

healthcheck:
  http: /health
  interval_seconds: 5
  start_period_seconds: 30

operations:
  - op: greet
    risk: read
```

Provisioning instructions in `docs/manual-testing.md`: operator creates `Tools` vault → adds `hello-rest` item → adds `API_KEY` field (with any test value) → provisions the toolyard's read-only Connect token. Standard one-time setup.

## Implementation order

Vertical slices, each independently testable:

1. **Project setup** — folder, `pyproject.toml`, README, `.gitignore`. Pick deps: pydantic, httpx, pyyaml, pytest, pytest-asyncio. Verify imports.
2. **`models.py` + `schema.py` + `test_schema.py`** — validate the schema against valid and invalid fixtures. This is foundational: if the schema is wrong, everything else breaks.
3. **`config.py`** — env var loading.
4. **`registry.py` + `test_registry.py`** — walk a fixture `tests/fixtures/tools/` directory, yield descriptors. Test with valid + invalid mixed.
5. **`secrets.py` + `test_secrets.py`** — `MockSecretResolver` + `resolve_and_write_secrets` against `tmp_path`. Verify file modes, content, dir creation. No actual 1Password needed.
6. **`docker_driver.py` + `MockDockerDriver` + `test_docker_driver.py` (mock tests only)** — verify the mock driver records calls correctly. Real Docker tests come in step 9.
7. **`healthcheck.py` + `test_healthcheck.py`** — use httpx + a tiny local FastAPI app in the test to validate the probe loop.
8. **`lifecycle.py` + `test_lifecycle.py`** — `up`, `down`, `restart`, `list_tools` against mock driver + mock resolver + temp filesystem. This is the bulk of the logic; spend time on tests here.
9. **`CLIDockerDriver`** — implement against real Docker. Smoke test: build the official `hello-world` image equivalent, verify run/stop work. Skip if Docker isn't installed; mark tests with a fixture/skip decorator.
10. **`cli.py` + `test_cli.py`** — wire everything together. Test by invoking the CLI as a subprocess against a tmp environment.
11. **The `hello-rest` tool** — write the Dockerfile, app.py, toolyard.yaml under `../../tools/hello-rest/`. Build it standalone first (`docker build`), confirm it runs.
12. **End-to-end manual test** — follow `docs/manual-testing.md`. Provision 1Password, run `toolyard up hello-rest`, curl the endpoint, verify the secret was read.

## Testing

- pytest, pytest-asyncio if any async creeps in (most of this is sync).
- Use `tmp_path` for filesystem fixtures (secrets dir, tools dir).
- Mock-based unit tests for everything except `CLIDockerDriver` and the end-to-end manual test.
- For `CLIDockerDriver`: a couple of smoke tests that actually exercise Docker, marked with `@pytest.mark.docker` and skipped by default. Run them manually in the bring-up.
- Don't mock `subprocess.run` for `CLIDockerDriver` tests — that's the actual behavior being tested.
- Don't test `op-connect-shim`'s internals — that's already tested. Just test that `ConnectSecretResolver` calls into it correctly via a mock at the boundary.

Target coverage: every public function in `schema.py`, `secrets.py`, `registry.py`, `lifecycle.py`, `healthcheck.py` has at least one test. `cli.py` end-to-end tests cover each subcommand.

## Manual testing procedure

Write `docs/manual-testing.md` with detailed steps. Minimum coverage:

1. **One-time setup**:
   - In 1Password Connect, create the `Tools` vault.
   - Add a new item `hello-rest` to that vault. Add a field `API_KEY` with any non-empty test value (e.g., `"this-is-a-test-key"`).
   - In the Connect admin, generate two tokens scoped to the `Tools` vault: one read-only, one write-only (the second is optional — only needed if you also add a writable-field tool).
   - Save the read-only token to `/etc/toolyard/op-connect-read.token` (mode 0600). Optional: same for the write token.

2. **Environment**:
   ```sh
   export TOOLYARD_OP_CONNECT_HOST=http://<your-connect-host>:19080
   export TOOLYARD_OP_CONNECT_TOKEN_FILE=/etc/toolyard/op-connect-read.token
   export TOOLYARD_TOOLS_DIR=./tools
   export TOOLYARD_SECRETS_DIR=/tmp/toolyard-secrets        # for dev; /var/lib/toolyard/secrets for prod
   ```

3. **Validate the example tool**:
   ```sh
   toolyard validate ./tools/hello-rest
   # -> "tools/hello-rest/toolyard.yaml: ok"
   ```

4. **Inspect declared secrets**:
   ```sh
   toolyard secrets hello-rest
   # hello-rest declares 1 secret:
   #   api_key  ->  Tools/hello-rest/API_KEY  (read)
   ```

5. **Bring up the tool**:
   ```sh
   toolyard up hello-rest
   # hello-rest: building... done. starting... ok. healthy.
   ```

6. **Verify secrets were resolved**:
   ```sh
   ls -la /tmp/toolyard-secrets/hello-rest/
   # -rw------- toolyard toolyard ... api_key
   cat /tmp/toolyard-secrets/hello-rest/api_key
   # this-is-a-test-key
   ```

7. **Verify inside the container**:
   ```sh
   docker exec toolyard-hello-rest ls /run/secrets
   # api_key

   docker exec toolyard-hello-rest cat /run/secrets/api_key
   # this-is-a-test-key

   docker exec toolyard-hello-rest env | grep OP_CONNECT
   # (no output — no Connect token because hello-rest has no writable fields)

   docker exec toolyard-hello-rest id
   # uid=10000 ... (non-root)
   ```

8. **Hit the tool**:
   ```sh
   curl http://127.0.0.1:5000/health
   # {"ok": true}

   curl -X POST http://127.0.0.1:5000/v1/actions/greet \
        -H "Content-Type: application/json" \
        -d '{"arguments": {"name": "you"}}'
   # {"result": "hello you"}
   ```

9. **List status**:
   ```sh
   toolyard ls
   # hello-rest    running    healthy    127.0.0.1:5000
   ```

10. **Restart picks up secret rotation**:
    - Update `Tools/hello-rest/API_KEY` in 1Password to a new value.
    - `toolyard restart hello-rest`.
    - `cat /tmp/toolyard-secrets/hello-rest/api_key` shows the new value.

11. **Tear down**:
    ```sh
    toolyard down hello-rest
    docker ps -a | grep toolyard-
    # (no rows)
    ```

12. **Add a new tool**:
    - Make a copy of `tools/hello-rest/` as `tools/hello-rest-2/`, edit `toolyard.yaml` `id: hello-rest-2`, change port to 5001.
    - Create a `Tools/hello-rest-2` item in 1Password with an `API_KEY` field.
    - `toolyard add ./tools/hello-rest-2 && toolyard up hello-rest-2`.
    - Verify it's reachable on `127.0.0.1:5001`.

## Acceptance criteria

- [ ] All unit tests pass (`pytest tests/`).
- [ ] Manual testing procedure passes end-to-end.
- [ ] `schema.py`, `secrets.py`, `lifecycle.py`, `registry.py`, `healthcheck.py` can be tested without Docker installed (verify by running just those test modules in a venv without `docker` on PATH).
- [ ] `secrets.py` can be tested without 1Password Connect running (uses `MockSecretResolver`).
- [ ] After `toolyard up hello-rest`, `docker exec toolyard-hello-rest env | grep OP_CONNECT` returns no output (no Connect token in a non-writable tool).
- [ ] After `toolyard up hello-rest`, `/run/secrets/api_key` inside the container has mode 0400 or 0444 (read-only) and the correct value.
- [ ] The container is **not reachable** from outside the host on port 5000 (only `127.0.0.1:5000`).
- [ ] The container runs as UID 10000 (non-root).
- [ ] `toolyard validate` exits non-zero with a clear message for an invalid `toolyard.yaml` (test cases: missing `id`, missing `entrypoint`, `id` collision, both `build` and `image` set, `secrets[].name == "_connect_token"`).
- [ ] `toolyard down` cleanly removes the container; running it again is idempotent.
- [ ] `toolyard restart` re-resolves secrets (verify by changing the 1Password value and observing the new value in `/run/secrets/`).
- [ ] No secret values appear in toolyard logs at any log level.
- [ ] The toolyard process itself does not retain secret values in memory after writing them to disk (audit by reading `secrets.py` — values should be local to the function, not stored on `self`).

## Out of scope (do NOT build)

- Real broker integration (broker's `HTTPDispatcher` / `MCPDispatcher` and registry-reading come in the next slice).
- The `mcp-stdio` adapter (the schema accepts `type: mcp-stdio`, but `lifecycle.up` should fail with a clear "not yet implemented in this slice" error for that type). `mcp-http` is similar to `rest` from the toolyard's perspective (just runs a container on a port) — you can support it transparently in `up`, but no example MCP tool in this slice.
- The writable-secrets opt-in path (`writable: true` declarations) — the schema accepts it, but `hello-rest` doesn't use it. Add a `TODO` and a clear error if `TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE` is required but unset. Full implementation is a small follow-up.
- Volumes for persistent tool data (schema-accepted but unexercised by `hello-rest`).
- `network: isolated` / `network: host` (schema-accepted, default `network: default` only).
- Docker Compose, multi-container tools, sidecars.
- Image registry support beyond `docker pull` of public images (no auth to private registries).
- Tool secrets rotation via the broker (out of scope for the toolyard).
- Anything in `agent-broker/` (the old monolith).
- The Discord approver bot.

## Notes for the implementer

- **`op-connect-shim` is stdlib-only** — no extra deps. Vendor it into your project (copy `op_connect_shim.py` from `../../lib/op-connect-shim/`) or path-import it. Either works.
- **Pydantic v2** is recommended for the schema; the syntax above assumes it.
- **YAML parser**: `PyYAML` (`yaml.safe_load`) is fine. Don't use `yaml.load` without `Loader=...`.
- **subprocess hygiene**: when shelling out to `docker`, pass arguments as a list (no shell), and `check=False` so you can inspect non-zero exits and emit helpful errors.
- **Container naming**: deterministic `toolyard-<id>` so `docker ps -f name=toolyard-` always finds yours.
- **Default UID 10000**: the user must exist in the container's `/etc/passwd` (the Dockerfile in `hello-rest` creates it via `useradd -u 10000`). For images that don't have the user, `docker run --user 10000:10000` still works but the user will be "nobody" — the tool's Dockerfile is responsible for making this work cleanly.
- **`/run/secrets` as bind mount**: it's a host-bind, not tmpfs. The toolyard creates the per-tool directory on disk. Container mounts it `:ro`.
- **No secret logging**: log a one-liner like `"hello-rest: resolved 1 secret"` — never the values. Validate this in tests.
- **Healthcheck failures aren't fatal**: per the design doc, log + mark unhealthy + leave the container running. Operator can `restart` if needed.
- **Idempotency**: `up` of a running tool should be a no-op (or, ideally, restart). `down` of a stopped tool is a no-op. Document the chosen behavior.
- **State file is a cache, not source of truth**: `runtime.json` is optional. The toolyard should be able to rebuild its view from `docker ps -f name=toolyard-*` if `runtime.json` is lost.
- **Don't grow this past ~500 LOC** (excluding tests, schema definitions, and the example tool). The toolyard is "a wrapper around `docker run` that reads YAML and knows where to mount secrets" — see [`../design/20-toolyard.md`](../design/20-toolyard.md). If you cross 600 LOC of toolyard source, stop and review what's bloating.

## When you're done

Original completion note requested:
- What was built and where.
- Anything that diverged from this plan and why.
- The exact one-time setup the operator needs (1Password vault, items, Connect tokens).
- The exact `toolyard` commands they'll run day-to-day.
- Any open questions or follow-ups for the next slice.

The next phase wires the toolyard to the broker: implements `HTTPDispatcher` + `MCPDispatcher` in the broker, gives the broker its own registry reader for `tools/<id>/toolyard.yaml`, and replaces `SyntheticDispatcher` as the default. The hello-rest tool you built here will become the first end-to-end test through the real system: agent → broker → tool → response, with policy + approval + audit all live.
