"""Dispatcher protocol + implementations.

The Dispatcher protocol is the swap seam for tool execution. The broker's
lifecycle/approval modules call ``dispatcher.dispatch(request, descriptor)``
and don't know which implementation is wired in.

Implementations:

- ``SyntheticDispatcher`` — dev stub, returns a synthetic result.
- ``HTTPDispatcher`` — forwards REST actions to ``http://<host>:<port>/v1/actions/<op>``.
- ``MCPDispatcher`` — wraps an action as a JSON-RPC ``tools/call`` and forwards
  to ``http://<host>:<port>/mcp``. Used when an mcp-http tool is invoked via
  the broker's ``/v1/actions/<tool>.<op>`` path.
- ``RoutingDispatcher`` — picks the right implementation based on the
  descriptor's ``type``. Becomes the broker's default dispatcher.

The ``/mcp/<tool>`` route in ``api.py`` blind-forwards JSON-RPC frames and
does **not** go through MCPDispatcher (it bypasses most of the action_request
lifecycle for non-``tools/call`` methods).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from broker.models import ActionRequest, DispatchResult, ToolDescriptor

logger = logging.getLogger(__name__)


# ── Protocol ─────────────────────────────────────────────────────────


class Dispatcher(Protocol):
    """Protocol for dispatching action requests to tool servers."""

    async def dispatch(
        self,
        request: ActionRequest,
        descriptor: ToolDescriptor | None,
    ) -> DispatchResult:
        """Execute a tool action and return the result."""
        ...


# ── Synthetic (dev fallback) ─────────────────────────────────────────


class SyntheticDispatcher:
    """Stub dispatcher that returns synthetic results.

    Used during development before real tool servers exist, and as a fallback
    when ``BROKER_DEFAULT_DISPATCHER=synthetic`` is set.

    Supports a debug override: if arguments contain ``__synthetic_outcome=fail``,
    returns a failure result.
    """

    async def dispatch(
        self,
        request: ActionRequest,
        descriptor: ToolDescriptor | None,
    ) -> DispatchResult:
        if request.arguments.get("__synthetic_outcome") == "fail":
            return DispatchResult(
                success=False,
                result=None,
                error="synthetic failure",
            )

        return DispatchResult(
            success=True,
            result={
                "synthetic": True,
                "tool": request.tool,
                "op": request.op,
                "arguments_echo": request.arguments,
            },
            error=None,
        )


# ── HTTP (REST tools) ────────────────────────────────────────────────


class HTTPDispatcher:
    """Forwards REST actions to a tool container's HTTP endpoint.

    Contract with the tool:

        POST http://<host>:<descriptor.port>/v1/actions/<request.op>
        Body: {
          "arguments": <request.arguments>,
          "reason":    <request.reason>,
          "broker_request_id": <request.id>,
          "caller": {"name": <caller>}
        }

        Tool returns a JSON object on success (any shape; broker stores it as
        the request's ``result``). Non-2xx responses are reported as failures.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        host: str = "127.0.0.1",
        timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._host = host
        self._timeout = timeout

    async def dispatch(
        self,
        request: ActionRequest,
        descriptor: ToolDescriptor | None,
    ) -> DispatchResult:
        if descriptor is None or descriptor.port is None:
            return DispatchResult(
                success=False,
                error="unknown_tool (no descriptor or port)",
            )

        url = f"http://{self._host}:{descriptor.port}/v1/actions/{request.op}"
        payload = {
            "arguments": request.arguments,
            "reason": request.reason,
            "broker_request_id": request.id,
            "caller": {"name": request.caller},
        }

        try:
            resp = await self._client.post(url, json=payload, timeout=self._timeout)
        except httpx.TimeoutException:
            return DispatchResult(success=False, error="tool_timeout")
        except (httpx.ConnectError, httpx.NetworkError) as e:
            return DispatchResult(
                success=False,
                error=f"tool_unreachable: {type(e).__name__}",
            )
        except httpx.HTTPError as e:
            return DispatchResult(
                success=False,
                error=f"tool_http_error: {type(e).__name__}",
            )

        if not resp.is_success:
            detail = resp.text[:500] if resp.text else ""
            return DispatchResult(
                success=False,
                error=f"tool_{resp.status_code}: {detail}",
            )

        try:
            body = resp.json()
        except Exception:
            return DispatchResult(
                success=False,
                error="tool_invalid_response: not JSON",
            )

        if not isinstance(body, dict):
            # Wrap non-dict responses so downstream storage (dict[str, Any]) is happy.
            return DispatchResult(success=True, result={"value": body})

        return DispatchResult(success=True, result=body)


# ── MCP (mcp-http tools, invoked via /v1/actions path) ──────────────


class MCPDispatcher:
    """Wraps an action as JSON-RPC ``tools/call`` and forwards to a tool's MCP endpoint.

    Used when an ``mcp-http`` tool is invoked through the broker's
    ``/v1/actions/<tool>.<op>`` path. Constructs the JSON-RPC frame, posts to
    ``http://<host>:<descriptor.port>/mcp``, and unwraps the response.

    For the broker's ``/mcp/<tool>`` blind-forwarder route (raw JSON-RPC),
    api.py does the forwarding directly without going through this dispatcher.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        host: str = "127.0.0.1",
        timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._host = host
        self._timeout = timeout

    async def dispatch(
        self,
        request: ActionRequest,
        descriptor: ToolDescriptor | None,
    ) -> DispatchResult:
        if descriptor is None or descriptor.port is None:
            return DispatchResult(
                success=False,
                error="unknown_tool (no descriptor or port)",
            )

        url = f"http://{self._host}:{descriptor.port}/mcp"
        frame = {
            "jsonrpc": "2.0",
            "id": request.id,
            "method": "tools/call",
            "params": {
                "name": request.op,
                "arguments": request.arguments,
            },
        }

        try:
            resp = await self._client.post(url, json=frame, timeout=self._timeout)
        except httpx.TimeoutException:
            return DispatchResult(success=False, error="tool_timeout")
        except (httpx.ConnectError, httpx.NetworkError) as e:
            return DispatchResult(
                success=False,
                error=f"tool_unreachable: {type(e).__name__}",
            )
        except httpx.HTTPError as e:
            return DispatchResult(
                success=False,
                error=f"tool_http_error: {type(e).__name__}",
            )

        if not resp.is_success:
            detail = resp.text[:500] if resp.text else ""
            return DispatchResult(
                success=False,
                error=f"tool_{resp.status_code}: {detail}",
            )

        try:
            envelope = resp.json()
        except Exception:
            return DispatchResult(
                success=False,
                error="tool_invalid_response: not JSON",
            )

        if not isinstance(envelope, dict):
            return DispatchResult(
                success=False,
                error="tool_invalid_response: JSON-RPC envelope must be an object",
            )

        # Protocol-level error
        if "error" in envelope:
            err = envelope["error"]
            msg = (
                err.get("message", "unknown") if isinstance(err, dict) else str(err)
            )
            return DispatchResult(
                success=False,
                error=f"mcp_jsonrpc_error: {msg}",
                result=err if isinstance(err, dict) else None,
            )

        result = envelope.get("result")
        if not isinstance(result, dict):
            return DispatchResult(success=True, result={"value": result})

        # MCP tools/call semantics: result.isError signals tool-level failure
        if result.get("isError"):
            return DispatchResult(
                success=False,
                error="tool reported error (isError=true)",
                result=result,
            )

        return DispatchResult(success=True, result=result)


# ── Routing (the default) ────────────────────────────────────────────


class RoutingDispatcher:
    """Picks the right concrete dispatcher based on the descriptor's ``type``.

    Becomes the broker's default dispatcher when
    ``BROKER_DEFAULT_DISPATCHER=routing`` (the default).

    Behavior:
      - If ``prefer_synthetic`` is set, always use ``synthetic`` (dev override).
      - If ``descriptor`` is None: use ``synthetic`` if available, else fail.
      - ``descriptor.type == "rest"``     → ``http``
      - ``descriptor.type == "mcp-http"`` → ``mcp``
      - ``descriptor.type == "mcp-stdio"`` → fail with "not yet supported"
      - Unknown type → fail with descriptive error
    """

    def __init__(
        self,
        *,
        http: HTTPDispatcher | None = None,
        mcp: MCPDispatcher | None = None,
        synthetic: SyntheticDispatcher | None = None,
        prefer_synthetic: bool = False,
    ) -> None:
        self._http = http
        self._mcp = mcp
        self._synthetic = synthetic
        self._prefer_synthetic = prefer_synthetic

    async def dispatch(
        self,
        request: ActionRequest,
        descriptor: ToolDescriptor | None,
    ) -> DispatchResult:
        # Dev override
        if self._prefer_synthetic and self._synthetic is not None:
            return await self._synthetic.dispatch(request, descriptor)

        # Unknown tool — fall back to synthetic if available (for tests with
        # BROKER_ALLOW_UNKNOWN_TOOLS=true). Otherwise refuse.
        if descriptor is None:
            if self._synthetic is not None:
                return await self._synthetic.dispatch(request, descriptor)
            return DispatchResult(
                success=False,
                error="unknown_tool",
            )

        if descriptor.type == "rest":
            if self._http is None:
                return DispatchResult(
                    success=False,
                    error="HTTPDispatcher not configured",
                )
            return await self._http.dispatch(request, descriptor)

        if descriptor.type == "mcp-http":
            if self._mcp is None:
                return DispatchResult(
                    success=False,
                    error="MCPDispatcher not configured",
                )
            return await self._mcp.dispatch(request, descriptor)

        if descriptor.type == "mcp-stdio":
            return DispatchResult(
                success=False,
                error="mcp-stdio not yet supported (toolyard adapter pending)",
            )

        return DispatchResult(
            success=False,
            error=f"unsupported tool type: {descriptor.type}",
        )
