"""Schema loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from toolyard.models import ToolDescriptor


def _yaml_path(path: Path) -> Path:
    return path / "toolyard.yaml" if path.is_dir() else path


def load_descriptor(path: Path) -> ToolDescriptor:
    yaml_path = _yaml_path(path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"{yaml_path}: not found")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{yaml_path}: expected a mapping")
    desc = validate_descriptor_dict(raw)
    desc.source_dir = yaml_path.parent
    return desc


def validate_descriptor_dict(data: dict[str, Any]) -> ToolDescriptor:
    try:
        return ToolDescriptor.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
