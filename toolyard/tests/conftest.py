from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def write_tool(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "toolyard.yaml"
    path.write_text(text, encoding="utf-8")
    return path
