from __future__ import annotations

import json
import socket

import pytest

from toolyard.audit import ToolyardAuditLog
from toolyard.schema import validate_descriptor_dict
from toolyard.secrets import MockSecretWriter
from toolyard.write_proxy import SecretUpdateDenied, WritableSecretProxy, WritableSecretUnixServer


def _descriptor():
    return validate_descriptor_dict({
        "id": "media",
        "type": "rest",
        "entrypoint": {"image": "media:latest", "port": 5000},
        "secrets": [
            {"name": "client_id", "field": "CLIENT_ID"},
            {"name": "refresh_token", "field": "REFRESH_TOKEN", "writable": True},
        ],
    })


def test_writable_proxy_updates_only_declared_writable_field(tmp_path):
    writer = MockSecretWriter()
    audit = ToolyardAuditLog(tmp_path / "audit.jsonl")
    proxy = WritableSecretProxy(descriptor=_descriptor(), writer=writer, audit_log=audit)

    result = proxy.update("refresh_token", "new-token", "oauth refresh")

    assert result.tool_id == "media"
    assert result.vault == "ToolServer"
    assert result.item == "media"
    assert result.field == "REFRESH_TOKEN"
    assert writer.updates == [("ToolServer", "media", "REFRESH_TOKEN", "new-token")]
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert events[0]["kind"] == "secret.update.completed"
    assert events[0]["field"] == "REFRESH_TOKEN"
    assert "new-token" not in json.dumps(events)


def test_writable_proxy_denies_undeclared_or_readonly_fields(tmp_path):
    writer = MockSecretWriter()
    proxy = WritableSecretProxy(
        descriptor=_descriptor(),
        writer=writer,
        audit_log=ToolyardAuditLog(tmp_path / "audit.jsonl"),
    )

    with pytest.raises(SecretUpdateDenied):
        proxy.update("client_id", "bad")
    with pytest.raises(SecretUpdateDenied):
        proxy.update("other_tool_refresh_token", "bad")

    assert writer.updates == []
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [event["kind"] for event in events] == ["secret.update.denied", "secret.update.denied"]


def test_unix_socket_update_endpoint_enforces_allowlist(tmp_path):
    writer = MockSecretWriter()
    server = WritableSecretUnixServer(
        socket_dir=tmp_path / "sockets" / "media",
        proxy=WritableSecretProxy(descriptor=_descriptor(), writer=writer),
    )
    server.start()
    try:
        status, body = _post_unix(
            server.socket_path,
            "/v1/secrets/refresh_token",
            {"value": "r2", "reason": "oauth refresh"},
        )
        assert status == 200
        assert body["field"] == "REFRESH_TOKEN"
        assert writer.updates == [("ToolServer", "media", "REFRESH_TOKEN", "r2")]

        status, body = _post_unix(
            server.socket_path,
            "/v1/secrets/client_id",
            {"value": "nope"},
        )
        assert status == 403
        assert body["error"] == "not_writable"
    finally:
        server.stop()


def _post_unix(path, target, payload):
    data = json.dumps(payload).encode("utf-8")
    request = (
        f"POST {target} HTTP/1.1\r\n"
        "Host: toolyard\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(data)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("utf-8") + data
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    raw = b"".join(chunks)
    header, body = raw.split(b"\r\n\r\n", 1)
    status = int(header.splitlines()[0].split()[1])
    return status, json.loads(body.decode("utf-8"))
