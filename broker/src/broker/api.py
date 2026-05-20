"""FastAPI HTTP API — the broker's external surface.

Routes per design/10-broker.md. Response shapes match the Discord bot's
HTTPBrokerClient expectations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
    if caller.profile != "approver" or not cfg.approver_signing_secret:
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
    """Check that the caller's profile allows a broker.* operation."""
    profile_data = policy.get_profile(caller.profile)
    if profile_data is None:
        raise HTTPException(status_code=403, detail="profile not found")

    allowed = profile_data.get("allowed_ops", [])
    import fnmatch
    full_op = f"broker.{op_name}"
    if not any(fnmatch.fnmatch(full_op, p) for p in allowed):
        raise HTTPException(
            status_code=403,
            detail=f"profile '{caller.profile}' does not allow '{full_op}'",
        )


# ── Serialization helper ────────────────────────────────────────────

def _serialize_request(req: ActionRequest) -> dict[str, Any]:
    """Serialize ActionRequest for JSON response.

    The bot expects these exact fields at the top level.
    """
    return req.model_dump(exclude_none=False)


# ── App factory ──────────────────────────────────────────────────────

def create_app(config: Config | None = None, dispatcher: Dispatcher | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _conn, _config, _dispatcher, _http_client

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

        # Initialize DB
        _config.state_dir.mkdir(parents=True, exist_ok=True)
        _conn = db.init_db(_config.db_path)

        # Load policies
        policy.load_profiles(_config.policies_dir)

        # Load registry
        registry.load_registry(_config.tools_dir)

        # Start timeout reaper
        reaper_task = asyncio.create_task(run_reaper(_conn, interval_seconds=30.0))

        logger.info(
            "broker started on %s (policies=%s, tools=%s, dispatcher=%s)",
            _config.bind_addr, _config.policies_dir, _config.tools_dir,
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

        Requires ``broker.registry.reload`` in the caller's profile.
        """
        _require_broker_op(caller, "registry.reload")
        cfg = get_config()
        new_registry = registry.load_registry(cfg.tools_dir)
        policy.reload_profiles()
        audit.record(
            get_conn(), "registry.reload",
            caller_id=caller.id,
            detail={"tool_count": len(new_registry)},
        )
        return {"reloaded": True, "tool_count": len(new_registry)}

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
            that the caller's profile permits this tool, then forwards blind.
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

        # Non-tools/call methods: blind forward if the profile allows this tool.
        profile_data = policy.get_profile(caller.profile)
        if profile_data is None or not _profile_allows_tool(profile_data, tool):
            return _jsonrpc_error(
                frame_id, JSONRPC_DENIED,
                f"profile '{caller.profile}' does not allow tool '{tool}'",
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


def _profile_allows_tool(profile: dict[str, Any], tool_id: str) -> bool:
    """Permissive check: does this profile let the caller see/list this tool?

    True if:
      - tool_id is in allowed_tools, OR
      - any allowed_ops / review_ops pattern starts with ``<tool_id>.``

    A `*.foo` pattern does NOT satisfy this check — operators should add the
    tool to allowed_tools explicitly to grant tools/list / initialize.
    """
    if tool_id in (profile.get("denied_tools") or []):
        return False
    if tool_id in (profile.get("allowed_tools") or []):
        return True
    prefix = f"{tool_id}."
    patterns = (profile.get("allowed_ops") or []) + (profile.get("review_ops") or [])
    return any(isinstance(p, str) and p.startswith(prefix) for p in patterns)


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
