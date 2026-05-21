"""Tool registry — reads <tools-root>/<id>/toolyard.yaml files.

Provides the broker with tool descriptors for policy (risk lookup), endpoint
validation (does this tool exist?), and dispatcher routing (what type? what
port?). Does NOT handle forwarding — that's the dispatcher's job.

Reload is atomic: a new in-memory map is built and only swapped in at the end.
Individual file parse errors are logged and skipped; they don't abort the
whole reload.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from broker.models import ToolDescriptor

logger = logging.getLogger(__name__)

# Module-level registry state
_registry: dict[str, ToolDescriptor] = {}
_tools_dir: Path | None = None

VALID_TYPES = frozenset({"rest", "mcp-http", "mcp-stdio"})


def _parse_descriptor(yaml_path: Path) -> ToolDescriptor | None:
    """Parse a single toolyard.yaml. Returns None if invalid or disabled."""
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except Exception:
        logger.exception("failed to read %s", yaml_path)
        return None

    if not data or not isinstance(data, dict):
        logger.warning("%s is empty or not a mapping — skipping", yaml_path)
        return None

    tool_id = data.get("id") or yaml_path.parent.name
    enabled = data.get("enabled", True)
    if not enabled:
        logger.info("tool %s is disabled — skipping", tool_id)
        return None

    tool_type = data.get("type", "rest")
    if tool_type not in VALID_TYPES:
        logger.warning(
            "tool %s has invalid type %r (expected one of %s) — skipping",
            tool_id, tool_type, sorted(VALID_TYPES),
        )
        return None

    entrypoint = data.get("entrypoint") or {}
    port = entrypoint.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        logger.warning(
            "tool %s has invalid entrypoint.port %r — skipping",
            tool_id, port,
        )
        return None

    try:
        return ToolDescriptor(
            id=tool_id,
            type=tool_type,
            description=data.get("description") or "",
            enabled=enabled,
            port=port,
            operations=data.get("operations") or [],
            risk_class_default=data.get("risk_class_default", "write"),
        )
    except Exception:
        logger.exception("failed to validate descriptor for tool %s", tool_id)
        return None


def load_registry(tools_dir: Path) -> dict[str, ToolDescriptor]:
    """Walk tools_dir, parse each toolyard.yaml, atomically swap the registry.

    Disabled tools (enabled: false) are skipped. Individual file errors are
    logged and skipped; the rest of the reload proceeds. The global registry
    is only swapped once the full walk completes.
    """
    global _registry, _tools_dir
    _tools_dir = tools_dir

    new_registry: dict[str, ToolDescriptor] = {}

    if not tools_dir.exists():
        logger.warning("tools dir %s does not exist — registry empty", tools_dir)
        _registry = new_registry
        return _registry

    for entry in sorted(tools_dir.iterdir()):
        if not entry.is_dir():
            continue
        yaml_path = entry / "toolyard.yaml"
        if not yaml_path.exists():
            continue
        desc = _parse_descriptor(yaml_path)
        if desc is None:
            continue
        if desc.id in new_registry:
            logger.warning(
                "duplicate tool id %s (second declaration in %s) — keeping first",
                desc.id, yaml_path,
            )
            continue
        new_registry[desc.id] = desc
        logger.info(
            "registered tool %s (type=%s, port=%s)",
            desc.id, desc.type, desc.port,
        )

    _registry = new_registry
    return _registry


def reload() -> dict[str, ToolDescriptor]:
    """Reload from the previously configured tools directory.

    Atomic: if reload fails entirely, the previous registry stays in place.
    Individual file errors during reload are skipped (logged), and the rest of
    the new registry is built normally.
    """
    if _tools_dir is None:
        return {}
    return load_registry(_tools_dir)


def get_tool(tool_id: str) -> ToolDescriptor | None:
    """Look up a tool by ID. Returns None for unknown or disabled tools."""
    return _registry.get(tool_id)


def list_tools() -> dict[str, ToolDescriptor]:
    """Return the full registry (only enabled tools)."""
    return dict(_registry)
