"""Small JSONL audit log for toolyard-local events."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ToolyardAuditLog:
    def __init__(self, path: Path):
        self.path = path

    def record(self, kind: str, **detail: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {"created_at": int(time.time()), "kind": kind, **detail}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
