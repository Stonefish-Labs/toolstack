"""FastAPI HTTP API — the broker's external surface.

Routes per design/10-broker.md. Response shapes match the Discord bot's
HTTPBrokerClient expectations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from broker import audit, db, registry, tokens, policy
from broker.approval import approve_request, reject_request
from broker.config import Config
from broker.dispatch import (
    Dispatcher,
    HTTPDispatcher,
    MCPDispatcher,
    RoutingDispatcher,
    SyntheticDispatcher,
)
from broker.lifecycle import (
    get_request_model,
    handle_action_request,
    list_request_models,
)
from broker.models import ActionRequest, Caller, RequestStatus
from broker.signing import SignatureError, verify_signature
from broker.timeouts import run_reaper
from broker.toolyard_client import ToolyardControlClient, ToolyardControlError

logger = logging.getLogger(__name__)

# JSON-RPC error codes used by the /mcp/<tool> forwarder.
# -32600 to -32603 are reserved; we use -32000..-32099 for server-defined errors.
JSONRPC_PENDING_REVIEW = -32000
JSONRPC_DENIED = -32001
JSONRPC_TOOL_UNREACHABLE = -32010
JSONRPC_TOOL_INVALID_RESPONSE = -32011
JSONRPC_INVALID_FRAME = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_UNKNOWN_TOOL = -32004

# ── Module-level state (set during lifespan) ─────────────────────────
_conn: sqlite3.Connection | None = None
_config: Config | None = None
_dispatcher: Dispatcher | None = None
_http_client: httpx.AsyncClient | None = None
_toolyard_client: ToolyardControlClient | None = None
_signature_nonces: dict[str, int] = {}


def get_conn() -> sqlite3.Connection:
    assert _conn is not None
    return _conn


def get_config() -> Config:
    assert _config is not None
    return _config


def get_dispatcher() -> Dispatcher:
    assert _dispatcher is not None
    return _dispatcher


def get_http_client() -> httpx.AsyncClient:
    assert _http_client is not None
    return _http_client


def get_toolyard_client() -> ToolyardControlClient:
    assert _toolyard_client is not None
    return _toolyard_client


def _build_default_dispatcher(
    config: Config, client: httpx.AsyncClient
) -> Dispatcher:
    """Construct the broker's default dispatcher based on config."""
    if config.default_dispatcher == "synthetic":
        return SyntheticDispatcher()

    http_disp = HTTPDispatcher(
        client=client,
        host=config.dispatch_host,
        timeout=config.dispatch_timeout_seconds,
    )
    mcp_disp = MCPDispatcher(
        client=client,
        host=config.dispatch_host,
        timeout=config.dispatch_timeout_seconds,
    )
    # When allow_unknown_tools is enabled (typically for dev), provide a
    # synthetic fallback so requests for unregistered tools still complete.
    synthetic = SyntheticDispatcher() if config.allow_unknown_tools else None
    return RoutingDispatcher(
        http=http_disp,
        mcp=mcp_disp,
        synthetic=synthetic,
    )


# ── Request/response models ─────────────────────────────────────────

class ActionBody(BaseModel):
    arguments: dict[str, Any] = {}
    reason: str | None = None


class ApproveBody(BaseModel):
    approver: str
    note: str | None = None


class RejectBody(BaseModel):
    approver: str
    reason: str | None = None


class ApprovalMessageBody(BaseModel):
    message_id: int
    last_status: str


PolicyEffect = Literal["allow", "review", "deny"]


class AdminCallerPolicyToolBody(BaseModel):
    operations: dict[str, PolicyEffect] = Field(default_factory=dict)


class AdminCallerPolicyBody(BaseModel):
    tools: dict[str, AdminCallerPolicyToolBody] = Field(default_factory=dict)
    broker_ops: list[str] = Field(default_factory=list)
    auto_grant_ttl_seconds: int | None = Field(default=None, ge=0)


class AdminCreateCallerBody(BaseModel):
    name: str
    policy: AdminCallerPolicyBody | None = None


# ── Auth dependency ──────────────────────────────────────────────────

async def require_auth(
    request: Request,
    authorization: str = Header(..., alias="Authorization"),
) -> Caller:
    """Verify bearer token. Returns Caller or raises 401."""
    conn = get_conn()
    caller = tokens.verify_bearer(authorization, conn)
    if caller is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    await _require_approver_signature(request, caller)
    return caller


async def _require_approver_signature(request: Request, caller: Caller) -> None:
    cfg = get_config()
    if not cfg.approver_signing_secret:
        return
    caller_policy = policy.caller_policy(get_conn(), caller.id)
    can_approve = policy.caller_allows_broker_op(caller_policy, "approve")
    can_reject = policy.caller_allows_broker_op(caller_policy, "reject")
    if not (can_approve or can_reject):
        return

    body = await request.body()
    target = _request_target(request)
    try:
        verify_signature(
            cfg.approver_signing_secret,
            request.method,
            target,
            body,
            request.headers,
            nonce_cache=_signature_nonces,
        )
    except SignatureError as exc:
        raise HTTPException(
            status_code=401,
            detail=f"invalid approver signature: {exc}",
        ) from exc


def _request_target(request: Request) -> str:
    query = request.url.query
    return f"{request.url.path}?{query}" if query else request.url.path


def _require_broker_op(caller: Caller, op_name: str) -> None:
    """Check that the caller's policy allows a broker.* operation."""
    full_op = f"broker.{op_name}"
    caller_policy = policy.caller_policy(get_conn(), caller.id)
    if not policy.caller_allows_broker_op(caller_policy, op_name):
        raise HTTPException(
            status_code=403,
            detail=f"caller '{caller.name}' does not allow '{full_op}'",
        )


# ── Serialization helper ────────────────────────────────────────────

def _serialize_request(req: ActionRequest) -> dict[str, Any]:
    """Serialize ActionRequest for JSON response.

    The bot expects these exact fields at the top level.
    """
    return req.model_dump(exclude_none=False)


def _serialize_approval_message(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": row["request_id"],
        "surface": row["surface"],
        "message_id": row["message_id"],
        "last_status": row["last_status"],
        "posted_at": row["posted_at"],
        "updated_at": row["updated_at"],
    }


def _public_caller(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row)


_CALLER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_BROKER_OP_RE = re.compile(r"^broker\.[A-Za-z0-9_.:-]+(?:\*)?$")


def _validate_caller_name(name: str) -> str:
    if not _CALLER_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="invalid caller name")
    return name


def _require_admin(caller: Caller, op_name: str) -> None:
    _require_broker_op(caller, f"admin.{op_name}")


def _reload_registry(caller: Caller) -> dict[str, Any]:
    cfg = get_config()
    new_registry = registry.load_registry(cfg.tools_dir)
    audit.record(
        get_conn(), "registry.reload",
        caller_id=caller.id,
        detail={"tool_count": len(new_registry)},
    )
    return {"reloaded": True, "tool_count": len(new_registry)}


def _tool_operations() -> dict[str, dict[str, dict[str, str]]]:
    tools = registry.list_tools()
    result: dict[str, dict[str, dict[str, str]]] = {}
    for tool_id, desc in tools.items():
        result[tool_id] = {}
        for operation in desc.operations:
            if not isinstance(operation, dict):
                continue
            op = operation.get("op")
            if not isinstance(op, str) or not op:
                continue
            risk = operation.get("risk")
            description = operation.get("description")
            result[tool_id][op] = {
                "risk": risk if risk in {"read", "write", "destructive"} else "write",
                "description": description if isinstance(description, str) else "",
            }
    return result


def _admin_tool_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for tool_id, desc in sorted(registry.list_tools().items()):
        operations = []
        for operation in desc.operations:
            if not isinstance(operation, dict):
                continue
            op = operation.get("op")
            if not isinstance(op, str) or not op:
                continue
            risk = operation.get("risk")
            description = operation.get("description")
            operations.append({
                "op": op,
                "risk": risk if risk in {"read", "write", "destructive"} else "write",
                "description": description if isinstance(description, str) else "",
            })
        payload[tool_id] = {
            "id": desc.id,
            "type": desc.type,
            "description": desc.description,
            "enabled": desc.enabled,
            "port": desc.port,
            "operations": operations,
        }
    return payload


def _structured_caller_policy(caller_row: dict[str, Any], policy_data: dict[str, Any]) -> dict[str, Any]:
    policy_data = policy.normalize_policy(policy_data)
    tools_payload: dict[str, Any] = {}
    current_tools = policy_data.get("tools") or {}
    for tool_id, operations in sorted(_tool_operations().items()):
        current_ops = current_tools.get(tool_id, {}).get("operations", {})
        tools_payload[tool_id] = {
            "operations": {
                op: current_ops.get(op, "deny")
                if current_ops.get(op, "deny") in {"allow", "review", "deny"}
                else "deny"
                for op in sorted(operations)
            }
        }
    return {
        "caller": caller_row["name"],
        "tools": tools_payload,
        "broker_ops": policy_data.get("broker_ops") or [],
        "auto_grant_ttl_seconds": policy_data.get("auto_grant_ttl_seconds"),
    }


def _policy_from_body(body: AdminCallerPolicyBody) -> dict[str, Any]:
    known = _tool_operations()
    tools: dict[str, dict[str, dict[str, str]]] = {}

    for tool_id, tool_policy in sorted(body.tools.items()):
        if tool_id not in known:
            raise HTTPException(status_code=400, detail=f"unknown tool: {tool_id}")
        operations: dict[str, str] = {}
        for op, effect in sorted(tool_policy.operations.items()):
            if op not in known[tool_id]:
                raise HTTPException(status_code=400, detail=f"unknown operation: {tool_id}.{op}")
            if effect in {"allow", "review"}:
                operations[op] = effect
        if operations:
            tools[tool_id] = {"operations": operations}

    broker_ops: list[str] = []
    for broker_op in body.broker_ops:
        if not _BROKER_OP_RE.fullmatch(broker_op):
            raise HTTPException(status_code=400, detail=f"invalid broker op: {broker_op}")
        broker_ops.append(broker_op)

    return policy.normalize_policy({
        "tools": tools,
        "broker_ops": broker_ops,
        "auto_grant_ttl_seconds": body.auto_grant_ttl_seconds,
    })


# ── App factory ──────────────────────────────────────────────────────

def create_app(
    config: Config | None = None,
    dispatcher: Dispatcher | None = None,
    toolyard_client: ToolyardControlClient | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _conn, _config, _dispatcher, _http_client, _toolyard_client

        # Use provided or load from env
        if config is not None:
            _config = config
        else:
            from broker.config import load_config
            _config = load_config()

        # Shared HTTP client for both dispatchers and /mcp/<tool> blind forwards
        _http_client = httpx.AsyncClient()

        # Wire the dispatcher: explicit param (tests) > config-driven default
        _dispatcher = dispatcher or _build_default_dispatcher(_config, _http_client)
        _toolyard_client = toolyard_client or ToolyardControlClient(_config.toolyard_control_socket)

        # Initialize DB
        _config.state_dir.mkdir(parents=True, exist_ok=True)
        _conn = db.init_db(_config.db_path)

        # Load registry
        tools = registry.load_registry(_config.tools_dir)

        # Start timeout reaper
        reaper_task = asyncio.create_task(run_reaper(_conn, interval_seconds=30.0))

        logger.info(
            "broker started on %s (state=%s, tools=%s, dispatcher=%s)",
            _config.bind_addr, _config.state_dir, _config.tools_dir,
            type(_dispatcher).__name__,
        )

        yield

        # Shutdown
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
        if _http_client:
            await _http_client.aclose()
        if _toolyard_client and toolyard_client is None:
            await _toolyard_client.aclose()
        _toolyard_client = None
        if _conn:
            _conn.close()

    app = FastAPI(title="Toolserver Broker", version="0.1.0", lifespan=lifespan)

    # ── Health (unauthenticated) ─────────────────────────────────

    @app.get("/v1/health")
    async def health():
        return {"ok": True}

    # ── Action invocation ────────────────────────────────────────

    @app.post("/v1/actions/{tool_op}")
    async def invoke_action(
        tool_op: str,
        body: ActionBody,
        caller: Caller = Depends(require_auth),
    ):
        """POST /v1/actions/<tool>.<op>"""
        parts = tool_op.rsplit(".", 1)
        if len(parts) != 2:
            raise HTTPException(
                status_code=400,
                detail="path must be <tool>.<op>",
            )
        tool, op = parts

        conn = get_conn()
        cfg = get_config()
        disp = get_dispatcher()

        request_model, result = await handle_action_request(
            caller=caller,
            tool=tool,
            op=op,
            arguments=body.arguments,
            reason=body.reason,
            conn=conn,
            dispatcher=disp,
            config=cfg,
        )

        if request_model.status == RequestStatus.COMPLETED:
            return {"result": result, **_serialize_request(request_model)}
        elif request_model.status == RequestStatus.PENDING_REVIEW:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=202,
                content={
                    "request_id": request_model.id,
                    "status": request_model.status.value,
                    **_serialize_request(request_model),
                },
            )
        elif request_model.status == RequestStatus.DENIED:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "denied",
                    "reason": request_model.policy_decision.get("reason", "policy denied")
                    if request_model.policy_decision else "policy denied",
                },
            )
        elif request_model.status == RequestStatus.FAILED:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "tool_failed",
                    "detail": request_model.error or "unknown error",
                },
            )
        else:
            return _serialize_request(request_model)

    # ── Request listing ──────────────────────────────────────────

    @app.get("/v1/requests")
    async def list_requests(
        status: str | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
        after_id: int | None = Query(None),
        caller: Caller = Depends(require_auth),
    ):
        """GET /v1/requests — list action requests."""
        _require_broker_op(caller, "list_requests")
        conn = get_conn()
        requests = list_request_models(
            conn, status=status, limit=limit, after_id=after_id,
        )
        return {"requests": [_serialize_request(r) for r in requests]}

    @app.get("/v1/requests/{request_id}")
    async def get_request_detail(
        request_id: int,
        caller: Caller = Depends(require_auth),
    ):
        """GET /v1/requests/<id> — single request detail."""
        conn = get_conn()
        req = get_request_model(conn, request_id)
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")

        # Allow if caller is the original requester OR has list_requests permission
        if req.caller_id != caller.id:
            _require_broker_op(caller, "list_requests")

        return _serialize_request(req)

    # ── Approval management ──────────────────────────────────────

    @app.post("/v1/requests/{request_id}/approve")
    async def approve(
        request_id: int,
        body: ApproveBody,
        caller: Caller = Depends(require_auth),
    ):
        """POST /v1/requests/<id>/approve"""
        _require_broker_op(caller, "approve")
        conn = get_conn()
        cfg = get_config()
        disp = get_dispatcher()

        result = await approve_request(
            request_id=request_id,
            approver=body.approver,
            note=body.note,
            conn=conn,
            dispatcher=disp,
            config=cfg,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="request not found")
        return _serialize_request(result)

    @app.post("/v1/requests/{request_id}/reject")
    async def reject(
        request_id: int,
        body: RejectBody,
        caller: Caller = Depends(require_auth),
    ):
        """POST /v1/requests/<id>/reject"""
        _require_broker_op(caller, "reject")
        conn = get_conn()

        result = await reject_request(
            request_id=request_id,
            approver=body.approver,
            reason=body.reason,
            conn=conn,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="request not found")
        return _serialize_request(result)

    # ── Approval UI message state ─────────────────────────────────

    @app.get("/v1/approval-messages")
    async def list_approval_messages(
        caller: Caller = Depends(require_auth),
    ):
        """GET /v1/approval-messages — list Discord message mappings."""
        _require_broker_op(caller, "approval_messages.read")
        rows = db.list_approval_messages(get_conn(), surface="discord")
        return {"messages": [_serialize_approval_message(r) for r in rows]}

    @app.get("/v1/approval-messages/{request_id}")
    async def get_approval_message(
        request_id: int,
        caller: Caller = Depends(require_auth),
    ):
        """GET /v1/approval-messages/<id> — get one Discord message mapping."""
        _require_broker_op(caller, "approval_messages.read")
        row = db.get_approval_message(get_conn(), request_id)
        if row is None or row["surface"] != "discord":
            raise HTTPException(status_code=404, detail="approval message not found")
        return _serialize_approval_message(row)

    @app.put("/v1/approval-messages/{request_id}")
    async def upsert_approval_message(
        request_id: int,
        body: ApprovalMessageBody,
        caller: Caller = Depends(require_auth),
    ):
        """PUT /v1/approval-messages/<id> — record a Discord message mapping."""
        _require_broker_op(caller, "approval_messages.write")
        conn = get_conn()
        if db.get_request(conn, request_id) is None:
            raise HTTPException(status_code=404, detail="request not found")
        row = db.upsert_approval_message(
            conn,
            request_id,
            surface="discord",
            message_id=body.message_id,
            last_status=body.last_status,
        )
        return _serialize_approval_message(row)

    @app.delete("/v1/approval-messages/{request_id}")
    async def delete_approval_message(
        request_id: int,
        caller: Caller = Depends(require_auth),
    ):
        """DELETE /v1/approval-messages/<id> — forget a Discord message mapping."""
        _require_broker_op(caller, "approval_messages.write")
        deleted = db.delete_approval_message(get_conn(), request_id)
        return {"deleted": deleted}

    # ── Audit ────────────────────────────────────────────────────

    @app.get("/v1/audit")
    async def list_audit(
        after_id: int | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
        caller: Caller = Depends(require_auth),
    ):
        """GET /v1/audit — list audit events."""
        _require_broker_op(caller, "audit")
        conn = get_conn()
        events = db.list_audit_events(conn, after_id=after_id, limit=limit)
        # Parse detail_json for response
        result = []
        for e in events:
            entry = dict(e)
            if entry.get("detail_json"):
                entry["detail"] = json.loads(entry["detail_json"])
            else:
                entry["detail"] = None
            del entry["detail_json"]
            result.append(entry)
        return {"events": result}

    # ── Registry ─────────────────────────────────────────────────

    @app.get("/v1/registry")
    async def list_registry(caller: Caller = Depends(require_auth)):
        """GET /v1/registry — tools known + addresses."""
        tools = registry.list_tools()
        return {
            "tools": {
                tid: desc.model_dump()
                for tid, desc in tools.items()
            }
        }

    @app.post("/v1/registry/reload")
    async def reload_registry(caller: Caller = Depends(require_auth)):
        """POST /v1/registry/reload — re-read toolyard.yaml files.

        Requires ``broker.registry.reload`` in the caller's policy.
        """
        _require_broker_op(caller, "registry.reload")
        return _reload_registry(caller)

    # ── Broker admin ────────────────────────────────────────────────

    @app.get("/v1/admin/tools")
    async def admin_list_tools(caller: Caller = Depends(require_auth)):
        _require_admin(caller, "tools.read")
        return {"tools": _admin_tool_payload()}

    @app.post("/v1/admin/tools/reload")
    async def admin_reload_tools(caller: Caller = Depends(require_auth)):
        _require_admin(caller, "tools.write")
        return _reload_registry(caller)

    @app.get("/v1/admin/toolyard/tools")
    async def admin_list_toolyard_tools(caller: Caller = Depends(require_auth)):
        _require_admin(caller, "tools.read")
        try:
            return await get_toolyard_client().list_tools()
        except ToolyardControlError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.post("/v1/admin/toolyard/tools/{tool_id}/{action}")
    async def admin_control_toolyard_tool(
        tool_id: str,
        action: str,
        caller: Caller = Depends(require_auth),
    ):
        if action not in {"start", "stop", "restart", "rebuild"}:
            raise HTTPException(status_code=400, detail="unsupported toolyard action")
        _require_admin(caller, "tools.write")
        try:
            result = await get_toolyard_client().control_tool(tool_id, action)
        except ToolyardControlError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        audit.record(
            get_conn(), f"toolyard.{action}",
            caller_id=caller.id,
            tool=tool_id,
            op=action,
            detail=result,
        )
        return result

    @app.get("/v1/admin/callers")
    async def admin_list_callers(
        include_revoked: bool = Query(False),
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "callers.read")
        rows = db.list_callers(get_conn(), include_revoked=include_revoked)
        return {"callers": [_public_caller(row) for row in rows]}

    @app.post("/v1/admin/callers")
    async def admin_create_caller(
        body: AdminCreateCallerBody,
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "callers.write")
        _validate_caller_name(body.name)
        conn = get_conn()
        try:
            caller_row = db.create_caller(conn, body.name)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="caller already exists") from exc
        policy.upsert_policy(
            conn,
            caller_row["id"],
            _policy_from_body(body.policy) if body.policy else policy.empty_policy(),
        )
        raw_token, hash_prefix = tokens.create_token_for_caller(conn, caller_row["id"])
        audit.record(
            conn,
            "token.created",
            caller_id=caller_row["id"],
            detail={
                "caller": body.name,
                "hash_prefix": hash_prefix,
                "created_by": caller.name,
            },
        )
        return {
            "caller": _public_caller(caller_row),
            "token": raw_token,
            "hash_prefix": hash_prefix,
        }

    @app.get("/v1/admin/callers/{caller_name}/policy")
    async def admin_get_caller_policy(
        caller_name: str,
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "callers.read")
        caller_row = db.get_caller_by_name(get_conn(), _validate_caller_name(caller_name))
        if caller_row is None:
            raise HTTPException(status_code=404, detail="caller not found")
        return _structured_caller_policy(
            caller_row,
            policy.caller_policy(get_conn(), caller_row["id"]),
        )

    @app.put("/v1/admin/callers/{caller_name}/policy")
    async def admin_put_caller_policy(
        caller_name: str,
        body: AdminCallerPolicyBody,
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "callers.write")
        conn = get_conn()
        caller_row = db.get_caller_by_name(conn, _validate_caller_name(caller_name))
        if caller_row is None:
            raise HTTPException(status_code=404, detail="caller not found")
        policy_data = _policy_from_body(body)
        policy.upsert_policy(conn, caller_row["id"], policy_data)
        audit.record(
            conn,
            "caller.policy.updated",
            caller_id=caller.id,
            detail={"caller": caller_name},
        )
        return _structured_caller_policy(caller_row, policy_data)

    @app.post("/v1/admin/callers/{caller_name}/refresh-token")
    async def admin_refresh_caller_token(
        caller_name: str,
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "tokens.write")
        conn = get_conn()
        caller_row = db.get_caller_by_name(conn, _validate_caller_name(caller_name))
        if caller_row is None or caller_row.get("revoked_at") is not None:
            raise HTTPException(status_code=404, detail="caller not found")
        revoked = db.revoke_tokens_for_caller(conn, caller_row["id"])
        raw_token, hash_prefix = tokens.create_token_for_caller(conn, caller_row["id"])
        audit.record(
            conn,
            "token.refreshed",
            caller_id=caller_row["id"],
            detail={
                "caller": caller_name,
                "revoked": revoked,
                "hash_prefix": hash_prefix,
                "created_by": caller.name,
            },
        )
        return {
            "caller": _public_caller(caller_row),
            "token": raw_token,
            "hash_prefix": hash_prefix,
            "revoked": revoked,
        }

    @app.delete("/v1/admin/callers/{caller_name}")
    async def admin_revoke_caller(
        caller_name: str,
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "callers.write")
        revoked = db.revoke_caller(get_conn(), caller_name)
        if revoked:
            audit.record(
                get_conn(),
                "token.revoked",
                caller_id=caller.id,
                detail={"caller": caller_name, "reason": "caller revoked via admin API"},
            )
        return {"revoked": revoked}

    @app.get("/v1/admin/tokens")
    async def admin_list_tokens(
        include_revoked: bool = Query(False),
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "tokens.read")
        rows = db.list_tokens(get_conn(), include_revoked=include_revoked)
        return {
            "tokens": [
                {
                    **{k: v for k, v in row.items() if k != "token_hash"},
                    "hash_prefix": row["token_hash"][:8],
                }
                for row in rows
            ]
        }

    @app.delete("/v1/admin/tokens/{hash_prefix}")
    async def admin_revoke_token(
        hash_prefix: str,
        caller: Caller = Depends(require_auth),
    ):
        _require_admin(caller, "tokens.write")
        count = db.revoke_token(get_conn(), hash_prefix)
        if count:
            audit.record(
                get_conn(),
                "token.revoked",
                caller_id=caller.id,
                detail={"hash_prefix": hash_prefix[:8], "count": count},
            )
        return {"revoked": count}

    # ── MCP blind forwarder ──────────────────────────────────────

    @app.post("/mcp/{tool}")
    async def mcp_forward(
        tool: str,
        request: Request,
        caller: Caller = Depends(require_auth),
    ):
        """POST /mcp/<tool> — blind JSON-RPC forwarder.

        Behavior:
          - For ``tools/call``: extracts ``params.name`` as the op, runs full
            policy + action_request lifecycle. Allow → forward (via the
            shared http client) → return tool's JSON-RPC response. Review →
            return JSON-RPC error -32000 with request_id in ``data``. Deny →
            return JSON-RPC error -32001.
          - For other methods (``initialize``, ``tools/list``, etc.): checks
            that the caller's policy permits this tool, then forwards blind.
            No action_request row is created.
        """
        try:
            frame = await request.json()
        except Exception:
            return _jsonrpc_error(None, JSONRPC_INVALID_FRAME, "invalid JSON-RPC frame")

        if not isinstance(frame, dict):
            return _jsonrpc_error(None, JSONRPC_INVALID_FRAME, "frame must be an object")

        frame_id = frame.get("id")
        method = frame.get("method")

        # Look up the tool
        descriptor = registry.get_tool(tool)
        if descriptor is None:
            return _jsonrpc_error(
                frame_id, JSONRPC_UNKNOWN_TOOL,
                f"unknown_tool: {tool}",
                http_status=404,
            )
        if descriptor.type not in ("mcp-http", "mcp-stdio"):
            return _jsonrpc_error(
                frame_id, JSONRPC_METHOD_NOT_FOUND,
                f"tool '{tool}' is not an MCP tool (type={descriptor.type})",
                http_status=400,
            )
        if descriptor.type == "mcp-stdio":
            return _jsonrpc_error(
                frame_id, JSONRPC_METHOD_NOT_FOUND,
                "mcp-stdio not yet supported (toolyard adapter pending)",
                http_status=501,
            )

        # tools/call: full policy + lifecycle
        if method == "tools/call":
            params = frame.get("params") or {}
            op = params.get("name") if isinstance(params, dict) else None
            args = params.get("arguments", {}) if isinstance(params, dict) else {}
            if not isinstance(op, str) or not op:
                return _jsonrpc_error(
                    frame_id, JSONRPC_INVALID_FRAME,
                    "tools/call missing params.name",
                )

            request_model, result = await handle_action_request(
                caller=caller,
                tool=tool,
                op=op,
                arguments=args if isinstance(args, dict) else {},
                reason=None,
                conn=get_conn(),
                dispatcher=get_dispatcher(),
                config=get_config(),
            )

            if request_model.status == RequestStatus.COMPLETED:
                return JSONResponse(
                    status_code=200,
                    content={
                        "jsonrpc": "2.0",
                        "id": frame_id,
                        "result": result,
                    },
                )
            if request_model.status == RequestStatus.PENDING_REVIEW:
                return _jsonrpc_error(
                    frame_id, JSONRPC_PENDING_REVIEW,
                    "pending_review",
                    http_status=202,
                    data={
                        "request_id": request_model.id,
                        "status": request_model.status.value,
                    },
                )
            if request_model.status == RequestStatus.DENIED:
                reason = (
                    request_model.policy_decision.get("reason", "policy denied")
                    if request_model.policy_decision else "policy denied"
                )
                return _jsonrpc_error(
                    frame_id, JSONRPC_DENIED, reason,
                    http_status=403,
                )
            if request_model.status == RequestStatus.FAILED:
                return _jsonrpc_error(
                    frame_id, JSONRPC_TOOL_UNREACHABLE,
                    request_model.error or "tool_failed",
                    http_status=502,
                )
            # Fallback for any unexpected terminal state
            return JSONResponse(
                status_code=200,
                content={
                    "jsonrpc": "2.0",
                    "id": frame_id,
                    "result": {"status": request_model.status.value},
                },
            )

        # Non-tools/call methods: blind forward if caller policy allows this tool.
        caller_policy = policy.caller_policy(get_conn(), caller.id)
        if not policy.caller_allows_tool(caller_policy, tool):
            return _jsonrpc_error(
                frame_id, JSONRPC_DENIED,
                f"caller '{caller.name}' does not allow tool '{tool}'",
                http_status=403,
            )

        return await _blind_forward_mcp(
            descriptor=descriptor,
            frame=frame,
            client=get_http_client(),
            host=get_config().dispatch_host,
            timeout=get_config().dispatch_timeout_seconds,
        )

    return app


def _jsonrpc_error(
    frame_id: Any,
    code: int,
    message: str,
    *,
    http_status: int = 200,
    data: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a JSON-RPC error response. http_status defaults to 200 (JSON-RPC
    spec says protocol-level errors are still HTTP 200), but we use 202 for
    pending_review, 403 for denied, 404 for unknown_tool, etc. so HTTP clients
    that don't understand JSON-RPC error codes see a sensible status.
    """
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return JSONResponse(
        status_code=http_status,
        content={"jsonrpc": "2.0", "id": frame_id, "error": err},
    )


async def _blind_forward_mcp(
    *,
    descriptor: Any,
    frame: dict[str, Any],
    client: httpx.AsyncClient,
    host: str,
    timeout: float,
) -> JSONResponse:
    """Forward a JSON-RPC frame as-is to the tool's /mcp endpoint."""
    url = f"http://{host}:{descriptor.port}/mcp"
    frame_id = frame.get("id")
    try:
        resp = await client.post(url, json=frame, timeout=timeout)
    except httpx.TimeoutException:
        return _jsonrpc_error(
            frame_id, JSONRPC_TOOL_UNREACHABLE, "tool_timeout",
            http_status=504,
        )
    except (httpx.ConnectError, httpx.NetworkError) as e:
        return _jsonrpc_error(
            frame_id, JSONRPC_TOOL_UNREACHABLE,
            f"tool_unreachable: {type(e).__name__}",
            http_status=502,
        )
    except httpx.HTTPError as e:
        return _jsonrpc_error(
            frame_id, JSONRPC_TOOL_UNREACHABLE,
            f"tool_http_error: {type(e).__name__}",
            http_status=502,
        )

    try:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception:
        return _jsonrpc_error(
            frame_id, JSONRPC_TOOL_INVALID_RESPONSE,
            "tool returned non-JSON response",
            http_status=502,
        )
