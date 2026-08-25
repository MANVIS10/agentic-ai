"""
Smoke test for Stage 21's embeddings + semantic search: confirms uploads
are embedded automatically, pre-existing NULL-embedding chunks can be
backfilled, and POST /documents/search returns relevant, correctly
ranked, correctly filtered results.

Not a pytest suite - same standalone-script convention as every other
stage's test file (asserts + prints, run directly with `python`). Uses
FastAPI's TestClient, so no running server is needed.

Ranking/threshold checks call the real OpenAI embeddings API (no mocking),
same as every prior stage's test file - OPENAI_API_KEY must be set, and
results depend on real (if clearly distinct-topic) embedding behavior.

Run with:
    python stage21_semantic_search/test_semantic_search.py
"""

import uuid

from fastapi.testclient import TestClient

from main import app, pg_conn

client = TestClient(app)

TEST_FILENAMES = [
    "test-embed-notes.txt",
    "test-solar-doc.txt",
    "test-hydro-doc.txt",
]


def clear_previous_test_documents():
    """Delete rows this test's fixed filenames left behind in a previous
    run (document_chunks cascade-deletes automatically via ON DELETE
    CASCADE). Same pattern as stage20_document_upload/test_document_upload.py.
    """
    pg_conn.execute("DELETE FROM documents WHERE filename = ANY(%s)", (TEST_FILENAMES,))


def upload(filename, text):
    return client.post(
        "/documents/upload",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
    )


def get_chunk_embeddings(document_id):
    return pg_conn.execute(
        "SELECT embedding FROM document_chunks WHERE document_id = %s", (document_id,)
    ).fetchall()


def run_upload_populates_embedding_check():
    # Deliberately off-topic from the solar/hydro fixtures used later in
    # run_relevant_query_and_scoping_checks, so this row never competes
    # with (or gets mistaken for) one of those two documents in a
    # similarity ranking.
    text = "Bicycles are a two-wheeled form of transportation powered by pedaling."
    response = upload("test-embed-notes.txt", text)
    print(f"[upload populates embedding] status={response.status_code} body={response.json()}\n")
    assert response.status_code == 200, response.text
    document_id = response.json()["document_id"]

    rows = get_chunk_embeddings(document_id)
    assert len(rows) > 0, "Expected at least one document_chunks row"
    for (embedding,) in rows:
        assert embedding is not None, "Expected every chunk uploaded through Stage 21 to have an embedding"
        assert len(embedding.to_list()) == 1536, "Expected a 1536-dimension embedding"


def run_backfill_populates_null_embedding_check():
    # Simulate a pre-Stage-21 row: insert directly via pg_conn with no
    # embedding at all, bypassing the upload route entirely.
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    pg_conn.execute(
        "INSERT INTO documents (id, filename, file_type, file_size_bytes, chunk_count) "
        "VALUES (%s, %s, %s, %s, %s)",
        (document_id, "test-embed-notes.txt", "txt", 100, 1),
    )
    pg_conn.execute(
        "INSERT INTO document_chunks (id, document_id, chunk_index, content) "
        "VALUES (%s, %s, %s, %s)",
        (chunk_id, document_id, 0, "Wind turbines convert kinetic energy into electricity."),
    )

    before = pg_conn.execute(
        "SELECT embedding FROM document_chunks WHERE id = %s", (chunk_id,)
    ).fetchone()
    assert before[0] is None, "Expected the manually-inserted chunk to start with no embedding"

    response = client.post("/documents/backfill-embeddings")
    body = response.json()
    print(f"[backfill] status={response.status_code} body={body}\n")
    assert response.status_code == 200, response.text
    assert body["embedded_count"] >= 1, "Expected at least the manually-inserted chunk to be embedded"

    after = pg_conn.execute(
        "SELECT embedding FROM document_chunks WHERE id = %s", (chunk_id,)
    ).fetchone()
    assert after[0] is not None, "Expected the chunk's embedding to be populated after backfill"
    assert len(after[0].to_list()) == 1536


def run_relevant_query_and_scoping_checks():
    solar_text = (
        "Solar panels use photovoltaic cells to convert sunlight directly into "
        "electricity. Efficiency depends on panel angle and sunlight exposure."
    )
    hydro_text = (
        "Hydropower plants use flowing or falling water to spin turbines connected "
        "to generators, producing electricity from the water's kinetic energy."
    )
    solar_response = upload("test-solar-doc.txt", solar_text)
    hydro_response = upload("test-hydro-doc.txt", hydro_text)
    assert solar_response.status_code == 200, solar_response.text
    assert hydro_response.status_code == 200, hydro_response.text
    solar_document_id = solar_response.json()["document_id"]
    hydro_document_id = hydro_response.json()["document_id"]

    # Relevant query ranks correctly: a solar-worded query's similarity to
    # the solar document should be higher than its similarity to the hydro
    # document. Compared directly via document_id-scoped searches rather
    # than asserting global rank #1, since this project runs every stage
    # against the same shared Postgres database - other stages' test
    # fixtures (e.g. Stage 20's own solar/hydro-flavored test text) persist
    # in document_chunks indefinitely and would otherwise make an unscoped
    # "is the top overall result X" assertion flaky.
    query = "How do photovoltaic solar panels generate power?"
    solar_search = client.post(
        "/documents/search",
        json={"query": query, "top_k": 1, "document_id": solar_document_id},
    ).json()
    hydro_search = client.post(
        "/documents/search",
        json={"query": query, "top_k": 1, "document_id": hydro_document_id},
    ).json()
    print(
        f"[search relevance] solar_similarity={solar_search['results'][0]['similarity']:.4f} "
        f"hydro_similarity={hydro_search['results'][0]['similarity']:.4f}\n"
    )
    assert len(solar_search["results"]) > 0 and len(hydro_search["results"]) > 0
    assert solar_search["results"][0]["similarity"] > hydro_search["results"][0]["similarity"], (
        "Expected a solar-worded query to be more similar to the solar document than the hydro document"
    )

    # top_k respected
    response = client.post(
        "/documents/search",
        json={"query": "renewable energy", "top_k": 1},
    )
    body = response.json()
    assert response.status_code == 200, response.text
    assert len(body["results"]) <= 1

    # similarity_threshold excludes low matches - unreachable threshold for
    # real, differently-worded embeddings.
    response = client.post(
        "/documents/search",
        json={"query": "renewable energy", "top_k": 10, "similarity_threshold": 0.999},
    )
    body = response.json()
    print(f"[similarity_threshold] status={response.status_code} results={body['results']}\n")
    assert response.status_code == 200
    assert body["results"] == [], "Expected an unreachable threshold to exclude every result"

    # document_id scoping - restrict to the hydro document only, confirm no
    # solar chunk leaks in even with a broad top_k.
    response = client.post(
        "/documents/search",
        json={"query": "electricity generation", "top_k": 10, "document_id": hydro_document_id},
    )
    body = response.json()
    print(f"[document_id scoping] status={response.status_code} results={len(body['results'])}\n")
    assert response.status_code == 200, response.text
    assert len(body["results"]) > 0
    for result in body["results"]:
        assert result["document_id"] == hydro_document_id, (
            f"Expected every result scoped to {hydro_document_id}, got {result['document_id']}"
        )


def run_empty_query_check():
    response = client.post("/documents/search", json={"query": "   "})
    print(f"[empty query] status={response.status_code} body={response.json()}\n")
    assert response.status_code == 400
    assert response.json()["detail"] == "Query text cannot be empty"


def run_invalid_top_k_check():
    response = client.post("/documents/search", json={"query": "solar power", "top_k": 0})
    print(f"[invalid top_k] status={response.status_code} body={response.json()}\n")
    assert response.status_code == 400
    assert "top_k" in response.json()["detail"]


def run_invalid_similarity_threshold_check():
    response = client.post(
        "/documents/search", json={"query": "solar power", "similarity_threshold": 1.5}
    )
    print(f"[invalid threshold] status={response.status_code} body={response.json()}\n")
    assert response.status_code == 400
    assert "similarity_threshold" in response.json()["detail"]


def run_unknown_document_id_check():
    response = client.post(
        "/documents/search",
        json={"query": "solar power", "document_id": str(uuid.uuid4())},
    )
    print(f"[unknown document_id] status={response.status_code} body={response.json()}\n")
    assert response.status_code == 404


def run_malformed_document_id_check():
    response = client.post(
        "/documents/search",
        json={"query": "solar power", "document_id": "not-a-real-uuid"},
    )
    print(f"[malformed document_id] status={response.status_code} body={response.json()}\n")
    assert response.status_code == 404, "Expected a malformed document_id to be treated as not found (404)"


def run_missing_query_field_check():
    response = client.post("/documents/search", json={})
    print(f"[missing query field] status={response.status_code}\n")
    assert response.status_code == 422  # automatic, via FastAPI/Pydantic


def run():
    clear_previous_test_documents()
    run_upload_populates_embedding_check()
    run_backfill_populates_null_embedding_check()
    run_relevant_query_and_scoping_checks()
    run_empty_query_check()
    run_invalid_top_k_check()
    run_invalid_similarity_threshold_check()
    run_unknown_document_id_check()
    run_malformed_document_id_check()
    run_missing_query_field_check()
    print(
        "All checks passed: uploads are embedded automatically, backfill "
        "populates pre-existing NULL embeddings, and /documents/search "
        "ranks, filters, and scopes results correctly."
    )


if __name__ == "__main__":
    run()
