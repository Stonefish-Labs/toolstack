"""Local HTTP control socket for toolyardd."""

from __future__ import annotations

import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


class ToolyardControlServer:
    def __init__(self, *, socket_path: Path, daemon: Any):
        self.socket_path = socket_path
        self.daemon = daemon
        self._server: _UnixHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        handler = _handler_for(self.daemon)
        self._server = _UnixHTTPServer(str(self.socket_path), handler)
        os.chmod(self.socket_path, 0o660)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="toolyard-control",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _handler_for(daemon: Any):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ToolyardControl/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            try:
                if path == "/v1/health":
                    self._json(200, {"ok": True})
                    return
                if path == "/v1/tools":
                    self._json(200, {"tools": [row.to_dict() for row in daemon.list_statuses()]})
                    return
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["v1", "tools"] and parts[3] == "logs":
                    tool_id = unquote(parts[2])
                    query = parse_qs(parsed.query)
                    tail = int((query.get("tail") or ["100"])[-1])
                    self._json(200, {"tool": tool_id, "logs": daemon.logs(tool_id, tail=tail)})
                    return
                self._json(404, {"detail": "not found"})
            except Exception as exc:
                self._error(exc)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            parts = parsed.path.strip("/").split("/")
            try:
                if len(parts) == 4 and parts[:2] == ["v1", "tools"]:
                    tool_id = unquote(parts[2])
                    action = parts[3]
                    self._json(200, daemon.control_tool(tool_id, action))
                    return
                self._json(404, {"detail": "not found"})
            except Exception as exc:
                self._error(exc)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _error(self, exc: Exception) -> None:
            if isinstance(exc, FileNotFoundError):
                self._json(404, {"detail": str(exc)})
            elif isinstance(exc, ValueError):
                self._json(400, {"detail": str(exc)})
            else:
                self._json(500, {"detail": str(exc)})

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
