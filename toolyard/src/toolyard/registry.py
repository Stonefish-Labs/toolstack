"""Filesystem registry for tools/<id>/toolyard.yaml."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from toolyard.models import ToolDescriptor
from toolyard.schema import load_descriptor


def walk_tools(tools_dir: Path) -> Iterator[ToolDescriptor]:
    if not tools_dir.exists():
        return
    for entry in sorted(tools_dir.iterdir()):
        yaml_path = entry / "toolyard.yaml"
        if entry.is_dir() and yaml_path.exists():
            try:
                yield load_descriptor(yaml_path)
            except Exception as exc:
                raise ValueError(f"{yaml_path}: {exc}") from exc


def reload_index(tools_dir: Path) -> dict[str, ToolDescriptor]:
    return {desc.id: desc for desc in walk_tools(tools_dir)}


def get_descriptor(tools_dir: Path, tool_id: str) -> ToolDescriptor | None:
    path = tools_dir / tool_id / "toolyard.yaml"
    return load_descriptor(path) if path.exists() else None
