# Toolyard Manual Testing

1. Create or confirm the Infisical project `ToolServer`.
2. In environment `prod`, create path `/hello-rest` with secret `API_KEY`.
3. Save the per-tool machine identity:

```bash
install -d -m 0700 /home/admin/.config/toolstack/infisical
install -m 0600 /dev/null /home/admin/.config/toolstack/infisical/hello-rest.env
```

The file contains:

```bash
INFISICAL_CLIENT_ID=...
INFISICAL_CLIENT_SECRET=...
```

4. Run a direct smoke test:

```bash
cd /home/admin/toolstack/toolyard
export TOOLYARD_INFISICAL_HOST=https://infisical.internal.example:19081
export TOOLYARD_INFISICAL_ENVIRONMENT=prod
export TOOLYARD_INFISICAL_CREDENTIALS_DIR=/home/admin/.config/toolstack/infisical
export TOOLYARD_TOOLS_DIR=/home/admin/.local/share/toolstack/tools
export TOOLYARD_STATE_DIR=/home/admin/.local/state/toolstack
export TOOLYARD_RUNTIME_DIR=/run/toolstack/toolyardd
install -d -m 0755 "$TOOLYARD_TOOLS_DIR"
cp -a /home/admin/toolstack/tools/hello-rest "$TOOLYARD_TOOLS_DIR/"
.venv/bin/toolyard validate "$TOOLYARD_TOOLS_DIR/hello-rest"
.venv/bin/toolyard secrets hello-rest
```

Use `toolyardd` for writable-secret tools so `/run/toolyard/secrets.sock` stays
available for the container lifetime.
