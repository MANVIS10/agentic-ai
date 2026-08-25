"""The body-size-limiting middleware and the unhandled-exception handler,
moved verbatim from stage25_react_ui/backend/main.py (lines 1233-1263).
Both stay `async def`, matching the original - async conversion of the
routes themselves is Phase 2, out of scope here.
"""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import MAX_JSON_BODY_BYTES


async def limit_json_body_size(request: Request, call_next):
    """Rejects an oversized request body before FastAPI/Pydantic ever
    parses it, based on the Content-Length header alone (never buffers or
    reads the body itself, so this adds negligible per-request overhead).
    Skips POST /documents/upload's multipart/form-data requests entirely -
    that route has its own, much larger, file-size handling (spec §3/§4).
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                length = None
            if length is not None and length > MAX_JSON_BODY_BYTES:
                return JSONResponse(
                    status_code=413, content={"detail": "Request body is too large"}
                )
    return await call_next(request)


async def unhandled_exception_handler(request: Request, exc: Exception):
    """Defense-in-depth net for anything that escapes a route's own
    try/except below (e.g. a failure while parsing the request itself).
    Never echoes exc's text back to the client - only logs it server-side.
    """
    print(f"[unhandled] {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "An unexpected error occurred."})
