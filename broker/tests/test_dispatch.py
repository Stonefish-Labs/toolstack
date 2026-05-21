"""Tests for dispatch.py — SyntheticDispatcher, HTTPDispatcher, MCPDispatcher, RoutingDispatcher.

Uses httpx.MockTransport (stdlib in the httpx library) to stub tool responses
without spinning up real servers — no Docker, no real tools.
"""

from __future__ import annotations

import json

import httpx
import pytest

from broker.dispatch import (
    HTTPDispatcher,
    MCPDispatcher,
    RoutingDispatcher,
    SyntheticDispatcher,
)
from broker.models import ActionRequest, RequestStatus, ToolDescriptor


def _make_request(**kwargs) -> ActionRequest:
    defaults = dict(
        id=1, caller="test", tool="t", op="o",
        status=RequestStatus.RUNNING,
    )
    defaults.update(kwargs)
    return ActionRequest(**defaults)


def _descriptor(**kwargs) -> ToolDescriptor:
    defaults = dict(id="t", type="rest", port=5000)
    defaults.update(kwargs)
    return ToolDescriptor(**defaults)


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── SyntheticDispatcher (preserved from prior slice) ────────────────


@pytest.mark.asyncio
async def test_synthetic_success():
    d = SyntheticDispatcher()
    req = _make_request(tool="media", op="get_state")
    result = await d.dispatch(req, None)
    assert result.success is True
    assert result.result["synthetic"] is True
    assert result.result["tool"] == "media"
    assert result.result["op"] == "get_state"


@pytest.mark.asyncio
async def test_synthetic_failure_override():
    d = SyntheticDispatcher()
    req = _make_request(arguments={"__synthetic_outcome": "fail"})
    result = await d.dispatch(req, None)
    assert result.success is False
    assert result.error == "synthetic failure"


@pytest.mark.asyncio
async def test_synthetic_echoes_arguments():
    d = SyntheticDispatcher()
    req = _make_request(arguments={"key": "value"})
    result = await d.dispatch(req, None)
    assert result.result["arguments_echo"] == {"key": "value"}


# ── HTTPDispatcher ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_dispatcher_success():
    """HTTPDispatcher posts to /v1/actions/<op> with the documented body shape."""
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": "hello you"})

    async with _make_client(handler) as client:
        d = HTTPDispatcher(client=client, host="testhost", timeout=5.0)
        req = _make_request(id=42, tool="hello-rest", op="greet",
                            arguments={"name": "you"}, reason="hi")
        result = await d.dispatch(req, _descriptor(id="hello-rest", port=5000))

    assert result.success is True
    assert result.result == {"result": "hello you"}
    assert captured["url"] == "http://testhost:5000/v1/actions/greet"
    body = captured["body"]
    assert body["arguments"] == {"name": "you"}
    assert body["reason"] == "hi"
    assert body["broker_request_id"] == 42
    assert body["caller"] == {"name": "test"}


@pytest.mark.asyncio
async def test_http_dispatcher_no_descriptor_fails():
    async with _make_client(lambda r: httpx.Response(200)) as client:
        d = HTTPDispatcher(client=client)
        result = await d.dispatch(_make_request(), None)
    assert result.success is False
    assert "unknown_tool" in result.error


@pytest.mark.asyncio
async def test_http_dispatcher_4xx_reports_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad input"})

    async with _make_client(handler) as client:
        d = HTTPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor())

    assert result.success is False
    assert "tool_400" in result.error


@pytest.mark.asyncio
async def test_http_dispatcher_5xx_reports_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    async with _make_client(handler) as client:
        d = HTTPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor())

    assert result.success is False
    assert "tool_503" in result.error


@pytest.mark.asyncio
async def test_http_dispatcher_connect_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with _make_client(handler) as client:
        d = HTTPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor())

    assert result.success is False
    assert "tool_unreachable" in result.error


@pytest.mark.asyncio
async def test_http_dispatcher_non_dict_wrapped():
    """If a tool returns a non-object JSON (e.g., a bare string), wrap it."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="just a string")

    async with _make_client(handler) as client:
        d = HTTPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor())

    assert result.success is True
    assert result.result == {"value": "just a string"}


@pytest.mark.asyncio
async def test_http_dispatcher_invalid_json():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json {{{")

    async with _make_client(handler) as client:
        d = HTTPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor())

    assert result.success is False
    assert "invalid_response" in result.error


# ── MCPDispatcher ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_dispatcher_success():
    """MCPDispatcher wraps the action as JSON-RPC tools/call and unwraps the result."""
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "content": [{"type": "text", "text": "2026-05-17T12:00:00Z"}],
                "isError": False,
            },
        })

    async with _make_client(handler) as client:
        d = MCPDispatcher(client=client, host="testhost", timeout=5.0)
        req = _make_request(id=7, tool="time-mcp", op="current_time",
                            arguments={"tz": "UTC"})
        result = await d.dispatch(req, _descriptor(id="time-mcp", type="mcp-http", port=5100))

    assert result.success is True
    assert result.result["content"][0]["text"] == "2026-05-17T12:00:00Z"
    assert captured["url"] == "http://testhost:5100/mcp"
    frame = captured["body"]
    assert frame["jsonrpc"] == "2.0"
    assert frame["method"] == "tools/call"
    assert frame["params"]["name"] == "current_time"
    assert frame["params"]["arguments"] == {"tz": "UTC"}
    assert frame["id"] == 7


@pytest.mark.asyncio
async def test_mcp_dispatcher_isError_reports_failure():
    """An MCP tool result with isError=true should be reported as a failure."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "tz not found"}],
                "isError": True,
            },
        })

    async with _make_client(handler) as client:
        d = MCPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor(type="mcp-http"))

    assert result.success is False
    assert "isError" in result.error
    assert result.result["isError"] is True


@pytest.mark.asyncio
async def test_mcp_dispatcher_jsonrpc_error():
    """A JSON-RPC envelope-level error is reported as a dispatch failure."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "tool not found"},
        })

    async with _make_client(handler) as client:
        d = MCPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor(type="mcp-http"))

    assert result.success is False
    assert "tool not found" in result.error


@pytest.mark.asyncio
async def test_mcp_dispatcher_unreachable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    async with _make_client(handler) as client:
        d = MCPDispatcher(client=client)
        result = await d.dispatch(_make_request(), _descriptor(type="mcp-http"))

    assert result.success is False
    assert "tool_unreachable" in result.error


# ── RoutingDispatcher ────────────────────────────────────────────────


class _Recorder:
    """Records dispatch calls for assertions."""

    def __init__(self, label: str):
        self.label = label
        self.calls: list[tuple[ActionRequest, ToolDescriptor | None]] = []

    async def dispatch(self, request, descriptor):
        from broker.models import DispatchResult
        self.calls.append((request, descriptor))
        return DispatchResult(success=True, result={"routed_to": self.label})


@pytest.mark.asyncio
async def test_routing_rest_goes_to_http():
    http_rec = _Recorder("http")
    mcp_rec = _Recorder("mcp")
    r = RoutingDispatcher(http=http_rec, mcp=mcp_rec)

    result = await r.dispatch(_make_request(), _descriptor(type="rest"))
    assert result.result == {"routed_to": "http"}
    assert len(http_rec.calls) == 1
    assert len(mcp_rec.calls) == 0


@pytest.mark.asyncio
async def test_routing_mcp_http_goes_to_mcp():
    http_rec = _Recorder("http")
    mcp_rec = _Recorder("mcp")
    r = RoutingDispatcher(http=http_rec, mcp=mcp_rec)

    result = await r.dispatch(_make_request(), _descriptor(type="mcp-http"))
    assert result.result == {"routed_to": "mcp"}
    assert len(http_rec.calls) == 0
    assert len(mcp_rec.calls) == 1


@pytest.mark.asyncio
async def test_routing_mcp_stdio_fails_clean():
    r = RoutingDispatcher(http=_Recorder("http"), mcp=_Recorder("mcp"))
    result = await r.dispatch(_make_request(), _descriptor(type="mcp-stdio"))
    assert result.success is False
    assert "mcp-stdio" in result.error


@pytest.mark.asyncio
async def test_routing_unknown_tool_uses_synthetic_fallback():
    syn = SyntheticDispatcher()
    r = RoutingDispatcher(http=None, mcp=None, synthetic=syn)
    result = await r.dispatch(_make_request(tool="ghost", op="op"), None)
    assert result.success is True
    assert result.result["synthetic"] is True


@pytest.mark.asyncio
async def test_routing_unknown_tool_no_synthetic_fails():
    r = RoutingDispatcher(http=None, mcp=None, synthetic=None)
    result = await r.dispatch(_make_request(), None)
    assert result.success is False
    assert result.error == "unknown_tool"


@pytest.mark.asyncio
async def test_routing_prefer_synthetic_dev_override():
    """BROKER_DEFAULT_DISPATCHER=synthetic forces synthetic even when real ones are wired."""
    http_rec = _Recorder("http")
    syn = SyntheticDispatcher()
    r = RoutingDispatcher(http=http_rec, mcp=None, synthetic=syn, prefer_synthetic=True)
    result = await r.dispatch(_make_request(), _descriptor(type="rest"))
    assert result.success is True
    assert result.result["synthetic"] is True
    assert len(http_rec.calls) == 0


@pytest.mark.asyncio
async def test_routing_unknown_type_fails():
    r = RoutingDispatcher(http=_Recorder("http"), mcp=_Recorder("mcp"))
    result = await r.dispatch(_make_request(), _descriptor(type="unknown-type"))
    assert result.success is False
    assert "unsupported tool type" in result.error
