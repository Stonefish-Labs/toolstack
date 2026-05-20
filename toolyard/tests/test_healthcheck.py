from __future__ import annotations

import httpx

from toolyard.healthcheck import wait_for_healthy
from toolyard.models import HealthcheckSpec


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


def test_wait_for_healthy_succeeds(monkeypatch):
    calls = iter([Response(503), Response(204)])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: next(calls))
    spec = HealthcheckSpec(http="/health", interval_seconds=0, start_period_seconds=1)
    assert wait_for_healthy(host_port=5000, spec=spec)


def test_wait_for_healthy_times_out(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response(503))
    spec = HealthcheckSpec(http="/health", interval_seconds=0, start_period_seconds=0)
    assert not wait_for_healthy(host_port=5000, spec=spec)
