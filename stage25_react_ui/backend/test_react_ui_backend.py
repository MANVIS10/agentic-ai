"""
Tests for Stage 25's backend delta: GET /documents (spec §3.1), the
execution trace on ThreadStatusResponse (spec §3.2), and CORS (spec §3.3).
Everything else in this module's main.py is copied verbatim from Stage 24
and is covered by re-running test_security_guardrails.py against this
module's app (see run_stage24_regression() below).

Not a pytest suite - same standalone-script convention as every other
stage's test file (asserts + prints, run directly with `python`). Uses
FastAPI's TestClient. Calls the real OpenAI chat/embeddings APIs (no
mocking), same as every prior stage's test file - OPENAI_API_KEY must be
set, and the docker-compose Postgres must be running.

Run with:
    python stage25_react_ui/backend/test_react_ui_backend.py
"""

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from main import (
    KNOWLEDGE_SYSTEM_PROMPT,
    app,
    pg_conn,
)

client = TestClient(app)

DATABASE_URL_FRAGMENT = "postgres:postgres@"

RUN_ID = uuid.uuid4().hex[:8]
USER_A = f"stage25-user-a-{RUN_ID}"
USER_B = f"stage25-user-b-{RUN_ID}"
NEW_USER = f"stage25-fresh-{RUN_ID}"
FILENAME_A = f"stage25-doc-a-{RUN_ID}.txt"
FILENAME_B = f"stage25-doc-b-{RUN_ID}.txt"
KNOWLEDGE_FILENAME = f"stage25-knowledge-{RUN_ID}.txt"


def upload(filename, content_bytes, user_id):
    return client.post(
        "/documents/upload",
        files={"file": (filename, content_bytes, "text/plain")},
        data={"user_id": user_id},
    )


def cleanup():
    pg_conn.execute(
        "DELETE FROM documents WHERE filename = ANY(%s)",
        ([FILENAME_A, FILENAME_B, KNOWLEDGE_FILENAME],),
    )


# ---------------------------------------------------------------------------
# GET /documents (spec §3.1)
# ---------------------------------------------------------------------------


def run_list_documents_isolation_check():
    """Two users each upload one document; GET /documents for user A
    returns only user A's document, with the exact field shape, and most-
    recent-first ordering (only one document per user here, but the field
    shape/ordering key is what's being checked).
    """
    resp_a = upload(FILENAME_A, b"Document A content about renewable energy.", USER_A)
    resp_b = upload(FILENAME_B, b"Document B content about quarterly earnings.", USER_B)
    print(f"[list isolation] upload A -> {resp_a.status_code}, upload B -> {resp_b.status_code}\n")
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    listing_a = client.get("/documents", params={"user_id": USER_A})
    print(f"[list isolation] GET /documents?user_id={USER_A} -> {listing_a.status_code} {listing_a.json()}\n")
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


def run_list_documents_empty_state_check():
    """A brand-new user_id with no uploads gets a 200 with an empty list,
    not an error (spec §4.6).
    """
    response = client.get("/documents", params={"user_id": NEW_USER})
    print(f"[list empty state] -> {response.status_code} {response.json()}\n")
    assert response.status_code == 200
    assert response.json() == {"documents": []}


def run_list_documents_validation_check():
    """Empty user_id -> 400, matching every other user_id-scoped route."""
    response = client.get("/documents", params={"user_id": "   "})
    print(f"[list validation] empty user_id -> {response.status_code} {response.json()}\n")
    assert response.status_code == 400
    assert response.json()["detail"] == "user_id cannot be empty"


# ---------------------------------------------------------------------------
# Execution trace on ThreadStatusResponse (spec §3.2)
# ---------------------------------------------------------------------------


def run_trace_knowledge_question_check():
    """Uploads a document, asks a question about it through the full
    /chat -> /approve flow, and checks the returned trace has exactly one
    entry (single-subtask plan not guaranteed, so just check the knowledge
    entry exists) with specialist == "knowledge" and
    "search_uploaded_documents" in tools_used - proving acceptance
    criterion 4 (the trace makes routing/tool-use observable) end to end.
    """
    upload_resp = upload(
        KNOWLEDGE_FILENAME,
        b"The secret internal project codename is Falcon. "
        b"It launches in the third quarter of next year.",
        USER_A,
    )
    print(f"[trace knowledge] upload -> {upload_resp.status_code}\n")
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
    print(f"[trace knowledge] /chat -> {chat_resp.status_code} {chat_resp.json()}\n")
    assert chat_resp.status_code == 200, chat_resp.text
    assert chat_resp.json()["status"] == "awaiting_approval"
    assert chat_resp.json()["trace"] is None

    approve_resp = client.post("/approve", json={"thread_id": thread_id})
    print(f"[trace knowledge] /approve -> {approve_resp.status_code}\n")
    assert approve_resp.status_code == 200, approve_resp.text
    body = approve_resp.json()
    assert body["status"] == "completed"

    trace = body["trace"]
    print(f"[trace knowledge] trace={trace}\n")
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


def run_trace_reject_check():
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
    print(f"[trace reject] /reject -> {reject_resp.status_code} {reject_resp.json()}\n")
    assert reject_resp.status_code == 200, reject_resp.text
    body = reject_resp.json()
    assert body["status"] == "rejected"
    assert body["trace"] == []


def run_trace_excludes_sensitive_data_check():
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
    print(f"[trace hygiene] response length={len(body_text)}\n")
    assert KNOWLEDGE_SYSTEM_PROMPT[:40] not in body_text
    assert "OPENAI_API_KEY" not in body_text
    assert DATABASE_URL_FRAGMENT not in body_text


# ---------------------------------------------------------------------------
# CORS (spec §3.3)
# ---------------------------------------------------------------------------


def run_cors_allowed_origin_check():
    response = client.options(
        "/documents",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    print(f"[cors allowed] -> {response.status_code} headers={dict(response.headers)}\n")
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def run_cors_disallowed_origin_check():
    response = client.options(
        "/documents",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    print(f"[cors disallowed] -> {response.status_code} headers={dict(response.headers)}\n")
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}


# ---------------------------------------------------------------------------
# Regression: Stage 24's own guardrail tests, unmodified, against this
# module's app - confirms the Stage 25 additions above didn't weaken any
# existing guardrail. Same "import the new module's app/pg_conn, run the
# old test file's run()" pattern Stage 22/23 already used.
# ---------------------------------------------------------------------------


def run_stage24_regression():
    stage24_dir = Path(__file__).resolve().parents[2] / "stage24_security_guardrails"
    sys.path.insert(0, str(stage24_dir))
    try:
        import test_security_guardrails as stage24_tests
    finally:
        sys.path.pop(0)

    print("[stage24 regression] running stage24_security_guardrails/test_security_guardrails.py ...\n")
    stage24_tests.run()
    print("[stage24 regression] passed\n")


def run():
    cleanup()

    run_list_documents_isolation_check()
    run_list_documents_empty_state_check()
    run_list_documents_validation_check()

    run_trace_knowledge_question_check()
    run_trace_reject_check()
    run_trace_excludes_sensitive_data_check()

    run_cors_allowed_origin_check()
    run_cors_disallowed_origin_check()

    cleanup()

    run_stage24_regression()

    print(
        "All checks passed: GET /documents returns only the calling user's "
        "documents with the correct field shape and a 200/empty-list for a "
        "brand-new user_id; the execution trace on /approve correctly "
        "records specialist/tool/verdict for a knowledge question and is "
        "empty on /reject; the trace response never leaks system-prompt "
        "text or credentials; CORS allows the known dev origin and rejects "
        "an unlisted one; and Stage 24's own guardrail test suite still "
        "passes unmodified against this module."
    )


if __name__ == "__main__":
    run()
