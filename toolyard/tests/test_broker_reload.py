from __future__ import annotations

import httpx
import pytest

from toolyard.broker_reload import notify_broker_reload
from toolyard.config import Config


def test_notify_broker_reload_uses_token_file(tmp_path, monkeypatch):
    token_file = tmp_path / "reload.token"
    token_file.write_text("abc123\n", encoding="utf-8")
    calls = []

    class Response:
        def raise_for_status(self):
            calls.append("raised")

    def fake_post(url, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)

    notify_broker_reload(Config(
        broker_reload_url="http://127.0.0.1:8765/v1/registry/reload",
        broker_reload_token_file=token_file,
    ))

    assert calls == [
        (
            "http://127.0.0.1:8765/v1/registry/reload",
            {"Authorization": "Bearer abc123"},
            10.0,
        ),
        "raised",
    ]


def test_notify_broker_reload_requires_token_when_url_set():
    with pytest.raises(ValueError, match="TOKEN_FILE"):
        notify_broker_reload(Config(broker_reload_url="http://broker/reload"))
