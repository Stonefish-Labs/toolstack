"""Fake Broker — FastAPI scaffolding for developing and testing the Discord bot.

This emulates the broker's approval-related endpoints so the bot can be
developed end-to-end without the real broker. All state is in-memory;
restart resets everything.

Start: uvicorn discord_approver.scaffolding.fake_broker:app --port 8765
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI(title="Fake Broker", version="0.1.0")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_BROKER_TOKEN: str | None = None


def _load_token() -> str:
    """Load the expected bearer token from file or env."""
    global _BROKER_TOKEN
    if _BROKER_TOKEN is not None:
        return _BROKER_TOKEN

    token_file = os.environ.get("FAKE_BROKER_TOKEN_FILE")
    if token_file and Path(token_file).exists():
        _BROKER_TOKEN = Path(token_file).read_text().strip()
    else:
        # Fallback: accept any token in dev mode
        _BROKER_TOKEN = os.environ.get("FAKE_BROKER_TOKEN", "dev-token")
    return _BROKER_TOKEN


def verify_token(authorization: str = Header(...)) -> str:
    """Validate the Bearer token."""
    expected = _load_token()
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer prefix")
    token = authorization[7:]
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------


class RequestStore:
    def __init__(self) -> None:
        self.requests: dict[int, dict] = {}
        self.messages: dict[int, dict] = {}
        self.next_id = 1

    def inject(self, data: dict) -> dict:
        req_id = self.next_id
        self.next_id += 1
        now = int(time.time())
        req = {
            "id": req_id,
            "caller": data.get("caller", "agent.hermes"),
            "tool": data.get("tool", "hello-rest"),
            "op": data.get("op", "skip_track"),
            "arguments": data.get("arguments", {}),
            "reason": data.get("reason"),
            "status": "pending_review",
            "risk": data.get("risk", "write"),
            "expires_at": now + data.get("ttl_seconds", 86400),
            "approver": None,
            "decision_note": None,
            "created_at": now,
            "updated_at": now,
        }
        self.requests[req_id] = req
        print(f"[INJECT] Request #{req_id}: {req['tool']}.{req['op']} ({req['risk']})")
        return req

    def reset(self) -> None:
        self.requests.clear()
        self.messages.clear()
        self.next_id = 1
        print("[RESET] All state cleared")


store = RequestStore()


# ---------------------------------------------------------------------------
# Real broker endpoints (the bot uses these)
# ---------------------------------------------------------------------------


@app.get("/v1/requests")
async def list_requests(
    status: str | None = None,
    after_id: int | None = None,
    limit: int = 50,
    _token: str = Depends(verify_token),
) -> list[dict]:
    results = []
    for req in store.requests.values():
        if status and req["status"] != status:
            continue
        if after_id is not None and req["id"] <= after_id:
            continue
        results.append(req)
    results.sort(key=lambda r: r["id"])
    return results[:limit]


@app.get("/v1/requests/{request_id}")
async def get_request(
    request_id: int,
    _token: str = Depends(verify_token),
) -> dict:
    req = store.requests.get(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


class ApproveBody(BaseModel):
    approver: str
    note: str | None = None


@app.post("/v1/requests/{request_id}/approve")
async def approve_request(
    request_id: int,
    body: ApproveBody,
    _token: str = Depends(verify_token),
) -> dict:
    req = store.requests.get(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve request in state '{req['status']}'",
        )
    req["status"] = "approved"
    req["approver"] = body.approver
    req["decision_note"] = body.note
    req["updated_at"] = int(time.time())
    print(f"[APPROVE] Request #{request_id} by {body.approver} (note: {body.note})")
    return req


class RejectBody(BaseModel):
    approver: str
    reason: str | None = None


@app.post("/v1/requests/{request_id}/reject")
async def reject_request(
    request_id: int,
    body: RejectBody,
    _token: str = Depends(verify_token),
) -> dict:
    req = store.requests.get(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reject request in state '{req['status']}'",
        )
    req["status"] = "rejected"
    req["approver"] = body.approver
    req["decision_note"] = body.reason
    req["updated_at"] = int(time.time())
    print(f"[REJECT] Request #{request_id} by {body.approver} (reason: {body.reason})")
    return req


@app.get("/v1/approval-messages")
async def list_approval_messages(
    _token: str = Depends(verify_token),
) -> dict:
    messages = sorted(store.messages.values(), key=lambda m: m["request_id"])
    return {"messages": messages}


@app.get("/v1/approval-messages/{request_id}")
async def get_approval_message(
    request_id: int,
    _token: str = Depends(verify_token),
) -> dict:
    msg = store.messages.get(request_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="Approval message not found")
    return msg


class ApprovalMessageBody(BaseModel):
    message_id: int
    last_status: str


@app.put("/v1/approval-messages/{request_id}")
async def upsert_approval_message(
    request_id: int,
    body: ApprovalMessageBody,
    _token: str = Depends(verify_token),
) -> dict:
    if request_id not in store.requests:
        raise HTTPException(status_code=404, detail="Request not found")
    now = int(time.time())
    existing = store.messages.get(request_id)
    msg = {
        "request_id": request_id,
        "surface": "discord",
        "message_id": body.message_id,
        "last_status": body.last_status,
        "posted_at": existing["posted_at"] if existing else now,
        "updated_at": now,
    }
    store.messages[request_id] = msg
    return msg


@app.delete("/v1/approval-messages/{request_id}")
async def delete_approval_message(
    request_id: int,
    _token: str = Depends(verify_token),
) -> dict:
    deleted = store.messages.pop(request_id, None) is not None
    return {"deleted": deleted}


@app.get("/v1/health")
async def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Dev-only endpoints (NOT in the real broker)
# ---------------------------------------------------------------------------


class InjectBody(BaseModel):
    caller: str = "agent.hermes"
    tool: str = "hello-rest"
    op: str = "skip_track"
    arguments: dict[str, Any] = {}
    reason: str | None = None
    risk: str = "write"
    ttl_seconds: int = 86400


@app.post("/v1/_dev/inject")
async def dev_inject(body: InjectBody) -> dict:
    """DEV ONLY: Inject a fake pending request."""
    return store.inject(body.model_dump())


@app.post("/v1/_dev/expire/{request_id}")
async def dev_expire(request_id: int) -> dict:
    """DEV ONLY: Force-expire a pending request."""
    req = store.requests.get(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot expire request in state '{req['status']}'",
        )
    req["status"] = "expired"
    req["updated_at"] = int(time.time())
    print(f"[EXPIRE] Request #{request_id}")
    return req


@app.post("/v1/_dev/reset")
async def dev_reset() -> dict:
    """DEV ONLY: Wipe all in-memory state."""
    store.reset()
    return {"ok": True}
