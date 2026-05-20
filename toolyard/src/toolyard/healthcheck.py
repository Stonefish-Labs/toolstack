"""HTTP health probes."""

from __future__ import annotations

import time

import httpx

from toolyard.models import HealthcheckSpec


def wait_for_healthy(
    *, host_port: int, spec: HealthcheckSpec, timeout_seconds: float | None = None
) -> bool:
    deadline = time.monotonic() + (
        spec.start_period_seconds if timeout_seconds is None else timeout_seconds
    )
    url = f"http://127.0.0.1:{host_port}{spec.http}"
    while True:
        try:
            response = httpx.get(url, timeout=2)
            if 200 <= response.status_code < 300:
                return True
        except httpx.HTTPError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(spec.interval_seconds or 0.1, remaining))
