# Broker Panel

Broker Panel is the browser UI for Toolstack broker administration. It talks to
the broker admin API with a dedicated broker service token; it does not open the
broker SQLite database directly.

The panel manages concrete callers. From the caller policy screen you can
enable or review individual tool operations, see operation descriptions from
`toolyard.yaml`, grant broker control ops, revoke callers, and refresh a
caller's token by revoking the active token set and issuing a new one-time token.

## Configuration

```sh
export BROKER_PANEL_BIND_ADDR=127.0.0.1:8780
export BROKER_PANEL_BROKER_URL=http://127.0.0.1:8765
export BROKER_PANEL_BROKER_TOKEN_FILE=/home/admin/.config/toolstack/tokens/broker-panel.token
export BROKER_PANEL_USERNAME=admin
export BROKER_PANEL_PASSWORD_HASH_FILE=/home/admin/.config/toolstack/tokens/broker-panel-password.hash
export BROKER_PANEL_SESSION_SECRET_FILE=/home/admin/.config/toolstack/tokens/broker-panel-session.key
```

Generate a password hash:

```sh
broker-panel hash-password 'replace-me'
```

Run locally:

```sh
broker-panel serve
```
