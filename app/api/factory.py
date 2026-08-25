"""FastAPI app construction, moved from stage25_react_ui/backend/main.py
(lines 1205-1263): the FastAPI(...) call (same title/description strings),
CORS middleware, the body-size-limit middleware, and the unhandled-
exception handler.

The original ran init_schema()-equivalent DDL and compiled the outer
planner graph at module import time (main.py:751, 753). Here both happen
in a lifespan handler instead, so `import app.main` is safe with Postgres
unreachable - only actually calling the app (or running init_schema/
build_graph by hand) needs a live database.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware import limit_json_body_size, unhandled_exception_handler
from app.api.routers import chat as chat_router
from app.api.routers.chat import router as chat_routes
from app.api.routers.documents import router as documents_routes
from app.api.routers.health import router as health_routes
from app.config import settings
from app.db import get_checkpointer, init_schema
from app.graphs.planner import build_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    graph = build_graph(get_checkpointer())
    chat_router.set_graph(graph)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Stage 24: Multi-Agent Research Assistant API with Security Guardrails",
        description=(
            "FastAPI wrapper around Stage 23's planner + human-approval + "
            "supervisor/critic/specialist research graph plus per-user "
            "document upload and semantic search, checkpointed to PostgreSQL. "
            "This stage adds production-oriented guardrails: hardened file "
            "validation, input length limits, prompt-injection defense for "
            "retrieved document content, an output leak guard, and in-process "
            "rate limiting - without adding authentication infrastructure."
        ),
        lifespan=lifespan,
    )

    # CORS (spec §3.3) - a browser-hosted Vite dev server is a different origin
    # than this API, and a browser enforces CORS unlike curl/TestClient. Only
    # the known dev origin is allow-listed, never "*", so this doesn't weaken
    # any of Stage 24's other guardrails. CORSMiddleware answers the browser's
    # OPTIONS preflight automatically, before any route/rate-limiter/thread
    # lock runs.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    app.middleware("http")(limit_json_body_size)
    app.exception_handler(Exception)(unhandled_exception_handler)

    app.include_router(health_routes)
    app.include_router(chat_routes)
    app.include_router(documents_routes)

    return app
