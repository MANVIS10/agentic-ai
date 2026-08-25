"""Ported from stages/stage25_react_ui/backend/test_react_ui_backend.py, converted
from its assert+print script style into pytest functions. Per this port's
Task 8: only the imports and the function wrappers changed (`from main
import (...)` -> `from app.main import app` / `from app.db import
get_connection`, and each `run_*_check()` becomes a `test_*` function with
its prints dropped since pytest reports failures on its own) - every
assertion keeps its original expected value.

Every test here calls the real OpenAI chat/embeddings API (via
/documents/upload, /chat, /approve) and needs the real pgvector Postgres
from docker-compose - gated behind this project's `pg_available` and
`openai_available` fixtures (see tests/conftest.py and CLAUDE.md's
"app/ package tests" section), same as every other stage's test file that
touches the real API.

Not ported: the original's `run_stage24_regression()`, which re-runs
stage24_security_guardrails/test_security_guardrails.py against the app by
relying on `main.SOME_CONSTANT = ...`/`main.some_function = ...`
monkeypatching of a single flat module namespace (e.g.
`main.EXTRACTION_TIMEOUT_SECONDS = 0.1`, `main.extract_text = stub`,
`main.SEARCH_USER_RATE_LIMIT = (3, 2)`). That technique is incompatible
with this port's module layout: Tasks 1-6 already moved every constant and
function behind `from app.config import X` - style imports scoped to each
consuming module (app/ingestion/extract.py, app/api/routers/documents.py,
...), so patching a value in one place (e.g. `app.config.
SEARCH_USER_RATE_LIMIT`) would not reach the separate name a given module
already bound at import time. Reproducing that regression faithfully would
require either editing stage24_security_guardrails/test_security_guardrails.py
(forbidden - it's a frozen, read-only stage) or restructuring app/'s import
style Tasks 1-6 already settled on. Left out rather than faked.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.agents.prompts import KNOWLEDGE_SYSTEM_PROMPT
from app.config import settings
from app.main import app

DATABASE_URL_FRAGMENT = "postgres:postgres@"

RUN_ID = uuid.uuid4().hex[:8]
USER_A = f"stage25-user-a-{RUN_ID}"
USER_B = f"stage25-user-b-{RUN_ID}"
NEW_USER = f"stage25-fresh-{RUN_ID}"
FILENAME_A = f"stage25-doc-a-{RUN_ID}.txt"
FILENAME_B = f"stage25-doc-b-{RUN_ID}.txt"
KNOWLEDGE_FILENAME = f"stage25-knowledge-{RUN_ID}.txt"


@pytest.fixture(scope="module")
def client(pg_available, openai_available):
    """One TestClient shared by every test in this module, entered as a
    context manager so FastAPI's lifespan handler actually runs
    (init_schema() + build_graph()) before any request - app.main.app
    defers both to lifespan (app/api/factory.py) rather than running them
    at import time like the original.
    """
    with TestClient(app) as test_client:
        yield test_client


def upload(client, filename, content_bytes, user_id):
    return client.post(
        "/documents/upload",
        files={"file": (filename, content_bytes, "text/plain")},
        data={"user_id": user_id},
    )


@pytest.fixture(autouse=True, scope="module")
def cleanup_documents(client):
    """Deliberately uses a throwaway, plain SYNC psycopg connection (like
    conftest.py's pg_available fixture) rather than app.db's async pool.

    This fixture runs on pytest-asyncio's own event loop, while `client`'s
    ASGI lifespan (including app.db's pool) runs on TestClient's OWN
    separate portal-thread loop (anyio's blocking portal always uses its
    own background thread + loop, regardless of what's already running).
    Touching the app's pool from here would bind or reuse it across two
    different event loops, and app.db's pool - like a real server's, which
    only ever runs on ONE loop for its whole process lifetime - is not
    meant to survive that: the mismatch surfaces as "Task ... attached to a
    different loop" whenever it's later closed. A one-off sync connection
    sidesteps the whole question for this simple cleanup query.
    """
    import psycopg

    def _cleanup():
        with psycopg.connect(settings.database_url) as conn:
            conn.execute(
                "DELETE FROM documents WHERE filename = ANY(%s)",
                ([FILENAME_A, FILENAME_B, KNOWLEDGE_FILENAME],),
            )

    _cleanup()
    yield
    _cleanup()


# ---------------------------------------------------------------------------
# GET /documents (spec §3.1)
# ---------------------------------------------------------------------------


def test_list_documents_isolation(client):
    """Two users each upload one document; GET /documents for user A
    returns only user A's document, with the exact field shape, and most-
    recent-first ordering (only one document per user here, but the field
    shape/ordering key is what's being checked).
    """
    resp_a = upload(client, FILENAME_A, b"Document A content about renewable energy.", USER_A)
    resp_b = upload(client, FILENAME_B, b"Document B content about quarterly earnings.", USER_B)
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    listing_a = client.get("/documents", params={"user_id": USER_A})
    assert listing_a.status_code == 200
    body = listing_a.json()
    assert "documents" in body
    assert len(body["documents"]) == 1
    doc = body["documents"][0]
    assert set(doc.keys()) == {"document_id", "filename", "file_type", "chunk_count", "created_at"}
    assert doc["filename"] == FILENAME_A
    assert doc["file_type"] == "txt"
    assert doc["chunk_count"] > 0
    assert doc["document_id"] == resp_a.json()["document_id"]

    # user A's listing must never contain user B's document.
    assert all(d["filename"] != FILENAME_B for d in body["documents"])


def test_list_documents_empty_state(client):
    """A brand-new user_id with no uploads gets a 200 with an empty list,
    not an error (spec §4.6).
    """
    response = client.get("/documents", params={"user_id": NEW_USER})
    assert response.status_code == 200
    assert response.json() == {"documents": []}


def test_list_documents_validation(client):
    """Empty user_id -> 400, matching every other user_id-scoped route."""
    response = client.get("/documents", params={"user_id": "   "})
    assert response.status_code == 400
    assert response.json()["detail"] == "user_id cannot be empty"


# ---------------------------------------------------------------------------
# Execution trace on ThreadStatusResponse (spec §3.2)
# ---------------------------------------------------------------------------


def test_trace_knowledge_question(client):
    """Uploads a document, asks a question about it through the full
    /chat -> /approve flow, and checks the returned trace has exactly one
    entry (single-subtask plan not guaranteed, so just check the knowledge
    entry exists) with specialist == "knowledge" and
    "search_uploaded_documents" in tools_used - proving acceptance
    criterion 4 (the trace makes routing/tool-use observable) end to end.
    """
    upload_resp = upload(
        client,
        KNOWLEDGE_FILENAME,
        b"The secret internal project codename is Falcon. "
        b"It launches in the third quarter of next year.",
        USER_A,
    )
    assert upload_resp.status_code == 200, upload_resp.text

    thread_id = f"stage25-trace-thread-{RUN_ID}"
    chat_resp = client.post(
        "/chat",
        json={
            "question": "What is the codename of the secret internal project mentioned in my uploaded documents, and when does it launch?",
            "thread_id": thread_id,
            "user_id": USER_A,
        },
    )
    assert chat_resp.status_code == 200, chat_resp.text
    assert chat_resp.json()["status"] == "awaiting_approval"
    assert chat_resp.json()["trace"] is None

    approve_resp = client.post("/approve", json={"thread_id": thread_id})
    assert approve_resp.status_code == 200, approve_resp.text
    body = approve_resp.json()
    assert body["status"] == "completed"

    trace = body["trace"]
    assert trace is not None
    assert len(trace) == len(body["subtasks"])

    knowledge_entries = [e for e in trace if e["specialist"] == "knowledge"]
    assert knowledge_entries, "Expected at least one subtask routed to the Knowledge Agent"
    assert any("search_uploaded_documents" in e["tools_used"] for e in knowledge_entries), (
        "Expected the knowledge specialist entry to record search_uploaded_documents"
    )

    for entry in trace:
        assert set(entry.keys()) == {
            "subtask", "specialist", "tools_used", "status", "verdict", "retry_count",
        }
        assert entry["status"] == "completed"
        assert entry["verdict"] in ("pass", "retry")
        assert isinstance(entry["retry_count"], int)
        assert entry["specialist"] in ("research", "knowledge", "analysis")


def test_trace_reject(client):
    """A rejected plan returns trace: [] - no research ran, so there's
    nothing to trace.
    """
    thread_id = f"stage25-trace-reject-{RUN_ID}"
    chat_resp = client.post(
        "/chat",
        json={"question": "What is 2 + 2?", "thread_id": thread_id, "user_id": USER_A},
    )
    assert chat_resp.status_code == 200, chat_resp.text

    reject_resp = client.post("/reject", json={"thread_id": thread_id})
    assert reject_resp.status_code == 200, reject_resp.text
    body = reject_resp.json()
    assert body["status"] == "rejected"
    assert body["trace"] == []


def test_trace_excludes_sensitive_data(client):
    """The trace's serialized response never contains the Knowledge
    Agent's system prompt text, the raw OPENAI_API_KEY, or DATABASE_URL -
    a direct string-search assertion mirroring Stage 24's own error-
    hygiene test style (spec §3.2's exclusion list).
    """
    thread_id = f"stage25-trace-hygiene-{RUN_ID}"
    chat_resp = client.post(
        "/chat",
        json={"question": "What is 5 * 5?", "thread_id": thread_id, "user_id": USER_A},
    )
    assert chat_resp.status_code == 200, chat_resp.text
    approve_resp = client.post("/approve", json={"thread_id": thread_id})
    assert approve_resp.status_code == 200, approve_resp.text

    body_text = approve_resp.text
    assert KNOWLEDGE_SYSTEM_PROMPT[:40] not in body_text
    assert "OPENAI_API_KEY" not in body_text
    assert DATABASE_URL_FRAGMENT not in body_text


# ---------------------------------------------------------------------------
# CORS (spec §3.3)
# ---------------------------------------------------------------------------


def test_cors_allowed_origin(client):
    response = client.options(
        "/documents",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_disallowed_origin(client):
    response = client.options(
        "/documents",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}
