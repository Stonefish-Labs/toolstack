# Toolyard Manual Testing

1. Create or confirm a `ToolServer` vault in 1Password Connect.
2. Create item `hello-rest` with field `API_KEY`.
3. Save host tokens:

```bash
install -d -m 0700 /home/admin/.config/toolstack/tokens
install -m 0600 /dev/null /home/admin/.config/toolstack/tokens/op-connect-read.token
install -m 0600 /dev/null /home/admin/.config/toolstack/tokens/op-connect-readwrite.token
```

4. Run a direct smoke test:

```bash
cd /home/admin/toolstack/toolyard
export TOOLYARD_OP_CONNECT_HOST=http://CONNECT-HOST:19080
export TOOLYARD_OP_CONNECT_TOKEN_FILE=/home/admin/.config/toolstack/tokens/op-connect-read.token
export TOOLYARD_OP_CONNECT_WRITE_TOKEN_FILE=/home/admin/.config/toolstack/tokens/op-connect-readwrite.token
export TOOLYARD_TOOLS_DIR=/home/admin/toolstack/tools
export TOOLYARD_STATE_DIR=/home/admin/toolstack/toolyard/state
export TOOLYARD_RUNTIME_DIR=/run/toolstack/toolyardd
.venv/bin/toolyard validate /home/admin/toolstack/tools/hello-rest
.venv/bin/toolyard secrets hello-rest
```

Use `toolyardd` for writable-secret tools so `/run/toolyard/secrets.sock` stays
available for the container lifetime.
