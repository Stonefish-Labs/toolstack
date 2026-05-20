"""Time MCP server — minimal MCP-HTTP implementation.

Exposes two tools (current_time, time_in) over JSON-RPC at POST /mcp.
Implemented directly with FastAPI to keep the dependency surface small
and make the MCP-HTTP protocol explicit. The broker forwards JSON-RPC
frames blind; this server responds in kind.
"""

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


app = FastAPI(title="time-mcp")


PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "time-mcp", "version": "0.1.0"}

TOOLS = [
    {
        "name": "current_time",
        "description": "Return the current UTC time as an ISO 8601 string.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "time_in",
        "description": "Return the current time in the given IANA timezone.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tz": {
                    "type": "string",
                    "description": "IANA timezone name, e.g. 'America/New_York'.",
                },
            },
            "required": ["tz"],
            "additionalProperties": False,
        },
    },
]


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def _ok(frame_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": frame_id, "result": result}


def _err(frame_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": frame_id,
        "error": {"code": code, "message": message},
    }


def _handle_call(frame_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}

    if name == "current_time":
        return _ok(frame_id, _text_result(datetime.now(timezone.utc).isoformat()))

    if name == "time_in":
        tz = args.get("tz")
        if not isinstance(tz, str) or not tz:
            return _ok(
                frame_id,
                _text_result("tz argument is required", is_error=True),
            )
        try:
            zone = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            return _ok(
                frame_id,
                _text_result(f"unknown timezone: {tz}", is_error=True),
            )
        return _ok(frame_id, _text_result(datetime.now(zone).isoformat()))

    return _err(frame_id, -32601, f"unknown tool: {name}")


@app.post("/mcp")
async def mcp(request: Request) -> JSONResponse:
    try:
        frame = await request.json()
    except Exception:
        return JSONResponse(_err(None, -32700, "parse error"))

    if not isinstance(frame, dict):
        return JSONResponse(_err(None, -32600, "frame must be an object"))

    frame_id = frame.get("id")
    method = frame.get("method")
    params = frame.get("params") or {}

    if method == "initialize":
        return JSONResponse(_ok(frame_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }))

    if method == "tools/list":
        return JSONResponse(_ok(frame_id, {"tools": TOOLS}))

    if method == "tools/call":
        return JSONResponse(_handle_call(frame_id, params))

    return JSONResponse(_err(frame_id, -32601, f"unknown method: {method}"))


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}
