"""Per-tool writable secret update proxy."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from urllib.parse import unquote

from toolyard.audit import ToolyardAuditLog
from toolyard.models import SecretRef, ToolDescriptor
from toolyard.secrets import SecretWriter


class SecretUpdateDenied(PermissionError):
    pass


@dataclass(frozen=True)
class SecretUpdateResult:
    tool_id: str
    secret_name: str
    vault: str
    item: str
    field: str


class WritableSecretProxy:
    def __init__(
        self, *, descriptor: ToolDescriptor, writer: SecretWriter,
        audit_log: ToolyardAuditLog | None = None,
    ):
        self.descriptor = descriptor
        self.writer = writer
        self.audit_log = audit_log
        self._writable: dict[str, SecretRef] = {
            ref.name: ref for ref in descriptor.secrets if ref.writable
        }

    def update(self, secret_name: str, value: str, reason: str | None = None) -> SecretUpdateResult:
        ref = self._writable.get(secret_name)
        if ref is None:
            self._audit(
                "secret.update.denied",
                secret_name=secret_name,
                reason="not declared writable",
            )
            raise SecretUpdateDenied(
                f"{self.descriptor.id}.{secret_name} is not declared writable"
            )

        item = ref.item or self.descriptor.id
        try:
            self.writer.update(ref.vault, item, ref.field, value)
        except Exception as exc:
            self._audit(
                "secret.update.failed",
                secret_name=secret_name,
                vault=ref.vault,
                item=item,
                field=ref.field,
                reason=reason,
                error=str(exc),
            )
            raise

        self._audit(
            "secret.update.completed",
            secret_name=secret_name,
            vault=ref.vault,
            item=item,
            field=ref.field,
            reason=reason,
        )
        return SecretUpdateResult(self.descriptor.id, secret_name, ref.vault, item, ref.field)

    def _audit(self, kind: str, **detail) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(kind, tool_id=self.descriptor.id, **detail)


class _ThreadingUnixStreamServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True


class WritableSecretUnixServer:
    def __init__(self, *, socket_dir: Path, proxy: WritableSecretProxy):
        self.socket_dir = socket_dir
        self.socket_path = socket_dir / "secrets.sock"
        self.proxy = proxy
        self._server: _ThreadingUnixStreamServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        handler = _handler_for(self.proxy)
        self._server = _ThreadingUnixStreamServer(str(self.socket_path), handler)
        os.chmod(self.socket_path, 0o660)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


def _handler_for(proxy: WritableSecretProxy):
    class Handler(BaseHTTPRequestHandler):
        server_version = "toolyardd-secrets/0.1"

        def do_POST(self) -> None:
            prefix = "/v1/secrets/"
            if not self.path.startswith(prefix):
                self._send(404, {"error": "not_found"})
                return
            secret_name = unquote(self.path[len(prefix):])
            if "/" in secret_name or not secret_name:
                self._send(400, {"error": "invalid_secret_name"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_content_length"})
                return
            if length <= 0 or length > 1024 * 1024:
                self._send(400, {"error": "invalid_body_size"})
                return

            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._send(400, {"error": "invalid_json"})
                return
            value = payload.get("value") if isinstance(payload, dict) else None
            reason = payload.get("reason") if isinstance(payload, dict) else None
            if not isinstance(value, str):
                self._send(400, {"error": "value_required"})
                return
            if reason is not None and not isinstance(reason, str):
                self._send(400, {"error": "invalid_reason"})
                return

            try:
                result = proxy.update(secret_name, value, reason)
            except SecretUpdateDenied as exc:
                self._send(403, {"error": "not_writable", "detail": str(exc)})
                return
            except Exception as exc:
                self._send(502, {"error": "update_failed", "detail": str(exc)})
                return

            self._send(200, {
                "ok": True,
                "tool": result.tool_id,
                "secret": result.secret_name,
                "vault": result.vault,
                "item": result.item,
                "field": result.field,
            })

        def log_message(self, format: str, *args) -> None:
            return

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler
