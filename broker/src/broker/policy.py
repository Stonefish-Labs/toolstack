"""Policy engine — YAML ACL loader + decide() function.

The swap seam: v1 reads per-profile YAML files. Future versions can swap to
OPA, Cedar, or an agent evaluator without touching call sites.

Rule evaluation order (first match wins):
  1. denied_tools → deny
  2. denied_ops (glob) → deny
  3. Active grant exists → allow (no new grant)
  4. allowed_ops (glob) → allow
  5. review_ops (glob) → review
  6. allowed_tools + risk_class_default → allow/review/deny by risk
  7. Nothing matched → deny (fail closed)
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

import yaml

from broker.models import Grant, PolicyDecision, PolicyInput

logger = logging.getLogger(__name__)

# Cached profiles: profile_name → parsed YAML dict
_profiles_cache: dict[str, dict[str, Any]] = {}
_profiles_dir: Path | None = None


def load_profiles(policies_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all profile YAMLs from the given directory. Returns profile_name → dict."""
    global _profiles_cache, _profiles_dir
    _profiles_dir = policies_dir
    _profiles_cache = {}

    if not policies_dir.exists():
        logger.warning("policies dir %s does not exist", policies_dir)
        return _profiles_cache

    for path in sorted(policies_dir.glob("*.yaml")):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict) and "profile" in data:
                _profiles_cache[data["profile"]] = data
                logger.info("loaded profile %s from %s", data["profile"], path.name)
        except Exception:
            logger.exception("failed to load profile from %s", path)

    return _profiles_cache


def reload_profiles() -> dict[str, dict[str, Any]]:
    """Reload profiles from the previously configured directory."""
    if _profiles_dir is None:
        return {}
    return load_profiles(_profiles_dir)


def get_profile(name: str) -> dict[str, Any] | None:
    """Get a loaded profile by name."""
    return _profiles_cache.get(name)


def _glob_match(pattern: str, value: str) -> bool:
    """Case-sensitive fnmatch."""
    return fnmatch.fnmatch(value, pattern)


def _match_any(patterns: list[str], tool_op: str) -> bool:
    """Return True if any pattern matches tool_op."""
    return any(_glob_match(p, tool_op) for p in patterns)


def decide(inp: PolicyInput) -> PolicyDecision:
    """Evaluate policy for a given action request. The swap seam.

    Returns a PolicyDecision with effect, reason, risk, and optional grant_ttl.
    """
    profile_data = get_profile(inp.profile)
    if profile_data is None:
        return PolicyDecision(
            effect="deny",
            reason=f"profile '{inp.profile}' not found",
            risk="write",
        )

    tool_op = f"{inp.tool}.{inp.op}"

    # Extract rule lists (all optional)
    denied_tools: list[str] = profile_data.get("denied_tools", [])
    denied_ops: list[str] = profile_data.get("denied_ops", [])
    allowed_tools: list[str] = profile_data.get("allowed_tools", [])
    allowed_ops: list[str] = profile_data.get("allowed_ops", [])
    review_ops: list[str] = profile_data.get("review_ops", [])
    risk_defaults: dict[str, str] = profile_data.get("risk_class_default", {})
    grant_ttl: int | None = profile_data.get("auto_grant_ttl_seconds")

    # Infer risk for this operation (default: "write")
    risk = _infer_risk(inp.tool, inp.op, profile_data)

    # 1. Denied tools
    if inp.tool in denied_tools:
        return PolicyDecision(
            effect="deny",
            reason=f"tool '{inp.tool}' is denied in profile '{inp.profile}'",
            risk=risk,
        )

    # 2. Denied ops (glob)
    if _match_any(denied_ops, tool_op):
        return PolicyDecision(
            effect="deny",
            reason=f"op '{tool_op}' matches a denied pattern",
            risk=risk,
        )

    # 3. Active grant → allow (no new grant)
    if inp.active_grants:
        return PolicyDecision(
            effect="allow",
            reason="active grant exists",
            risk=risk,
            grant_ttl_seconds=None,  # Don't create a new grant
        )

    # 4. Allowed ops (glob) → allow
    if _match_any(allowed_ops, tool_op):
        return PolicyDecision(
            effect="allow",
            reason=f"op '{tool_op}' matches an allowed pattern",
            risk=risk,
            grant_ttl_seconds=grant_ttl,
        )

    # 5. Review ops (glob) → review
    if _match_any(review_ops, tool_op):
        return PolicyDecision(
            effect="review",
            reason=f"op '{tool_op}' requires review",
            risk=risk,
            grant_ttl_seconds=grant_ttl,
        )

    # 6. Tool is allowed → apply risk_class_default
    if inp.tool in allowed_tools:
        effect = risk_defaults.get(risk, "review")
        if effect not in ("allow", "review", "deny"):
            effect = "review"  # safety net
        return PolicyDecision(
            effect=effect,
            reason=f"tool '{inp.tool}' allowed, risk '{risk}' → {effect}",
            risk=risk,
            grant_ttl_seconds=grant_ttl if effect != "deny" else None,
        )

    # 7. Nothing matched → fail closed
    return PolicyDecision(
        effect="deny",
        reason=f"no rule matched for '{tool_op}' in profile '{inp.profile}'",
        risk=risk,
    )


def _infer_risk(tool: str, op: str, profile_data: dict[str, Any]) -> str:
    """Infer risk class for an operation. Default: 'write' (conservative).

    Heuristic for v1: ops starting with get/list/read/fetch/show/find/search are read.
    Ops containing delete/destroy/drop are destructive.
    Everything else → 'write'.
    """
    op_lower = op.lower()

    if op_lower.startswith(("get_", "list_", "read_", "fetch_", "show_", "find_", "search_")):
        return "read"
    if op_lower in {"user_info", "whoami"}:
        return "read"
    if any(kw in op_lower for kw in ("delete", "destroy", "drop", "purge", "remove")):
        return "destructive"
    return "write"
