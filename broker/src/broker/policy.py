"""Caller-scoped policy evaluation."""

from __future__ import annotations

import fnmatch
import json
from typing import Any

from broker import db
from broker.models import PolicyDecision, PolicyInput, ToolDescriptor

PolicyEffect = str
EMPTY_POLICY: dict[str, Any] = {
    "tools": {},
    "broker_ops": [],
    "auto_grant_ttl_seconds": None,
}


def empty_policy() -> dict[str, Any]:
    return json.loads(json.dumps(EMPTY_POLICY))


def normalize_policy(policy_data: dict[str, Any] | None) -> dict[str, Any]:
    """Return a normalized caller policy dict with safe defaults."""
    if not isinstance(policy_data, dict):
        return empty_policy()
    tools = policy_data.get("tools")
    broker_ops = policy_data.get("broker_ops")
    ttl = policy_data.get("auto_grant_ttl_seconds")
    return {
        "tools": tools if isinstance(tools, dict) else {},
        "broker_ops": broker_ops if isinstance(broker_ops, list) else [],
        "auto_grant_ttl_seconds": ttl if isinstance(ttl, int) and ttl >= 0 else None,
    }


def decode_policy_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return empty_policy()
    try:
        payload = json.loads(row.get("policy_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    policy = normalize_policy(payload)
    if row.get("auto_grant_ttl_seconds") is not None:
        policy["auto_grant_ttl_seconds"] = row["auto_grant_ttl_seconds"]
    return policy


def encode_policy(policy_data: dict[str, Any]) -> tuple[str, int | None]:
    policy = normalize_policy(policy_data)
    return json.dumps(policy, sort_keys=True), policy["auto_grant_ttl_seconds"]


def upsert_policy(conn, caller_id: int, policy_data: dict[str, Any]) -> dict[str, Any]:
    policy_json, ttl = encode_policy(policy_data)
    db.upsert_caller_policy(conn, caller_id, policy_json, ttl)
    return normalize_policy(policy_data)


def caller_policy(conn, caller_id: int) -> dict[str, Any]:
    return decode_policy_row(db.get_caller_policy(conn, caller_id))


def caller_allows_broker_op(policy_data: dict[str, Any], op_name: str) -> bool:
    full_op = f"broker.{op_name}"
    patterns = normalize_policy(policy_data).get("broker_ops") or []
    return any(isinstance(p, str) and fnmatch.fnmatch(full_op, p) for p in patterns)


def caller_allows_tool(policy_data: dict[str, Any], tool_id: str) -> bool:
    tool_policy = normalize_policy(policy_data).get("tools", {}).get(tool_id)
    if not isinstance(tool_policy, dict):
        return False
    operations = tool_policy.get("operations")
    if not isinstance(operations, dict):
        return False
    return any(effect in {"allow", "review"} for effect in operations.values())


def decide(inp: PolicyInput, policy_data: dict[str, Any] | None) -> PolicyDecision:
    """Evaluate a caller-scoped policy for one tool operation."""
    risk = _infer_risk(inp.tool, inp.op, inp.declared_risk)

    if inp.active_grants:
        return PolicyDecision(
            effect="allow",
            reason="active grant exists",
            risk=risk,
            grant_ttl_seconds=None,
        )

    policy = normalize_policy(policy_data)
    effect = _operation_effect(policy, inp.tool, inp.op)
    ttl = policy.get("auto_grant_ttl_seconds")
    tool_op = f"{inp.tool}.{inp.op}"

    if effect == "allow":
        return PolicyDecision(
            effect="allow",
            reason=f"caller '{inp.caller}' allows '{tool_op}'",
            risk=risk,
            grant_ttl_seconds=ttl,
        )
    if effect == "review":
        return PolicyDecision(
            effect="review",
            reason=f"caller '{inp.caller}' requires review for '{tool_op}'",
            risk=risk,
            grant_ttl_seconds=ttl,
        )
    return PolicyDecision(
        effect="deny",
        reason=f"caller '{inp.caller}' denies '{tool_op}'",
        risk=risk,
    )


def _operation_effect(policy_data: dict[str, Any], tool: str, op: str) -> PolicyEffect:
    tool_policy = policy_data.get("tools", {}).get(tool)
    if not isinstance(tool_policy, dict):
        return "deny"
    operations = tool_policy.get("operations")
    if not isinstance(operations, dict):
        return "deny"
    effect = operations.get(op, "deny")
    return effect if effect in {"allow", "review", "deny"} else "deny"


def _infer_risk(tool: str, op: str, declared_risk: str | None = None) -> str:
    if declared_risk in {"read", "write", "destructive"}:
        return declared_risk
    op_lower = op.lower()
    if op_lower.startswith(("get_", "list_", "read_", "fetch_", "show_", "find_", "search_")):
        return "read"
    if op_lower in {"user_info", "whoami"}:
        return "read"
    if any(kw in op_lower for kw in ("delete", "destroy", "drop", "purge", "remove")):
        return "destructive"
    return "write"


def tool_operations(tools: dict[str, ToolDescriptor]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for tool_id, desc in tools.items():
        result[tool_id] = {}
        for operation in desc.operations:
            if not isinstance(operation, dict):
                continue
            op = operation.get("op")
            if not isinstance(op, str) or not op:
                continue
            risk = operation.get("risk")
            result[tool_id][op] = risk if risk in {"read", "write", "destructive"} else "write"
    return result
