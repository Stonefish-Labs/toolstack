"""Timeout reaper — expires pending requests that have timed out.

Background task that runs every N seconds, finds pending_review requests
whose expires_at has passed, and transitions them to expired.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

from broker import audit, db
from broker.models import RequestStatus

logger = logging.getLogger(__name__)


async def expire_pending_requests(
    conn: sqlite3.Connection,
    now: int | None = None,
) -> int:
    """Find and expire pending_review requests past their expiration time.

    Returns the number of requests expired.
    """
    if now is None:
        now = int(time.time())

    expired_rows = db.find_expired_pending(conn, now)
    count = 0

    for row in expired_rows:
        db.update_request_status(
            conn, row["id"],
            status=RequestStatus.EXPIRED.value,
        )
        audit.record(
            conn, "request.expired",
            request_id=row["id"],
            caller_id=row["caller_id"],
            tool=row["tool"],
            op=row["op"],
            detail={"reason": "timeout"},
        )
        count += 1
        logger.info(
            "expired request %d (%s.%s) — timed out",
            row["id"], row["tool"], row["op"],
        )

    return count


async def run_reaper(
    conn: sqlite3.Connection,
    interval_seconds: float = 30.0,
) -> None:
    """Background loop that expires timed-out pending requests.

    Runs forever (until cancelled). Meant to be started as a FastAPI
    lifespan background task.
    """
    logger.info("timeout reaper started (interval=%ss)", interval_seconds)
    while True:
        try:
            count = await expire_pending_requests(conn)
            if count > 0:
                logger.info("reaper expired %d request(s)", count)
        except asyncio.CancelledError:
            logger.info("timeout reaper cancelled")
            raise
        except Exception:
            logger.exception("timeout reaper tick failed")
        await asyncio.sleep(interval_seconds)
