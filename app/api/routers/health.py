"""GET /health, moved verbatim from stage25_react_ui/backend/main.py
(lines 1266-1279)."""

from fastapi import APIRouter, HTTPException

from app.api.schemas import HealthResponse
from app.db import get_connection

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health():
    """Verify the API process is up AND its Postgres dependency is
    reachable - a plain "the process is running" check would pass even if
    the database (the thing every other endpoint actually depends on) were
    down. Deliberately never rate-limited (spec §9) - monitoring must not
    be throttled.
    """
    try:
        get_connection().execute("SELECT 1").fetchone()
    except Exception as exc:
        print(f"[/health] Database unavailable: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(status="ok", database="connected")
