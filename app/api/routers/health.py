"""GET /health, moved verbatim from stage25_react_ui/backend/main.py
(lines 1266-1279)."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.api.schemas import HealthResponse
from app.config import HEALTH_DB_TIMEOUT_SECONDS
from app.db import connection

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    """Verify the API process is up AND its Postgres dependency is
    reachable - a plain "the process is running" check would pass even if
    the database (the thing every other endpoint actually depends on) were
    down. Deliberately never rate-limited (spec §9) - monitoring must not
    be throttled, and never authenticated - a monitor should not need a
    credential to ask whether the process is alive.

    Bounded by HEALTH_DB_TIMEOUT_SECONDS. Without it an unreachable database
    made this endpoint HANG for the pool's own much longer connect timeout
    instead of answering; a platform health check reads that as a timeout,
    which says less than a prompt 503 does.
    """
    try:
        async with asyncio.timeout(HEALTH_DB_TIMEOUT_SECONDS):
            async with connection() as conn:
                await (await conn.execute("SELECT 1")).fetchone()
    except Exception:  # includes the TimeoutError asyncio.timeout raises
        logger.warning("[/health] Database unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(status="ok", database="connected")
