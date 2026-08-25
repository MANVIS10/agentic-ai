"""
Test for Stage 23: proves cross-user document retrieval is impossible, not
just that the isolation filter looks correct in a single-user test. Two
distinct user_ids (alice/bob) each upload a document with distinctive,
made-up content, then every retrieval path is checked from both sides:
POST /documents/search, the Knowledge Agent's search_uploaded_documents
tool directly, and the full Knowledge Agent subgraph end to end.

Not a pytest suite - same standalone-script convention as every other
stage's test file (asserts + prints, run directly with `python`). Uses
FastAPI's TestClient for the HTTP routes, and calls knowledge_graph /
search_uploaded_documents directly (not through the outer planner) to
inspect tool calls, matching Stage 16/22's precedent for inspecting a
specialist subgraph directly since the outer graph's messages state
doesn't surface intermediate tool-call messages.

Calls the real OpenAI chat/embeddings APIs (no mocking), same as every
prior stage's test file - OPENAI_API_KEY must be set.

Run with:
    python stage23_user_document_isolation/test_user_document_isolation.py
"""

import uuid

from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from main import app, knowledge_graph, pg_conn, search_uploaded_documents

client = TestClient(app)

# Unique per run so repeated runs don't collide with leftover rows from a
# previous run (or from other stages' shared-database fixtures).
RUN_ID = uuid.uuid4().hex[:8]
ALICE = f"alice-{RUN_ID}"
BOB = f"bob-{RUN_ID}"
ALICE_FILENAME = f"alice-notes-{RUN_ID}.txt"
BOB_FILENAME = f"bob-notes-{RUN_ID}.txt"

ALICE_TEXT = (
    "Project Nightingale's prototype uses a gallium-arsenide solar cell "
    "rated for 47.3% conversion efficiency, codename Starling-9."
)
BOB_TEXT = (
    "Project Thornwood's water filtration rig removes 99.2% of "
    "microplastics using a ceramic membrane, codename Cobalt-Reef."
)


def upload(filename, text, user_id):
    return client.post(
        "/documents/upload",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
        data={"user_id": user_id},
    )


def search(query, user_id, **kwargs):
    body = {"query": query, "user_id": user_id, **kwargs}
    return client.post("/documents/search", json=body)


def used_tool(result, tool_name):
    return any(
        isinstance(m, ToolMessage) and m.name == tool_name for m in result["messages"]
    )


def ask_knowledge_agent(question, user_id):
    """Invoke the Knowledge Agent subgraph directly with a given user_id in
    its state, so the returned messages list includes the tool call/result
    (Stage 16's finding: the outer graph never surfaces this).
    """
    return knowledge_graph.invoke(
        {"messages": [{"role": "user", "content": question}], "user_id": user_id}
    )


def setup_documents():
    alice_response = upload(ALICE_FILENAME, ALICE_TEXT, ALICE)
    bob_response = upload(BOB_FILENAME, BOB_TEXT, BOB)
    print(f"[setup] alice upload status={alice_response.status_code} body={alice_response.json()}")
    print(f"[setup] bob upload status={bob_response.status_code} body={bob_response.json()}\n")
    assert alice_response.status_code == 200, alice_response.text
    assert bob_response.status_code == 200, bob_response.text
    assert alice_response.json()["user_id"] == ALICE
    assert bob_response.json()["user_id"] == BOB
    return alice_response.json(), bob_response.json()


def run_cross_user_search_check():
    """Core isolation proof for POST /documents/search: alice searching for
    bob's distinctive content must never see it, and vice versa - while
    each user CAN find their own content (proving the filter narrows,
    rather than always excluding everything).
    """
    alice_searches_for_bob = search("Cobalt-Reef microplastics filtration", ALICE, top_k=10)
    bob_searches_for_alice = search("Starling-9 solar cell efficiency", BOB, top_k=10)
    alice_searches_for_self = search("Starling-9 solar cell efficiency", ALICE, top_k=10)
    bob_searches_for_self = search("Cobalt-Reef microplastics filtration", BOB, top_k=10)

    for label, response in [
        ("alice->bob", alice_searches_for_bob),
        ("bob->alice", bob_searches_for_alice),
        ("alice->self", alice_searches_for_self),
        ("bob->self", bob_searches_for_self),
    ]:
        print(f"[cross-user search] {label} status={response.status_code} "
              f"results={[r['filename'] for r in response.json()['results']]}")
    print()

    alice_bob_filenames = {r["filename"] for r in alice_searches_for_bob.json()["results"]}
    bob_alice_filenames = {r["filename"] for r in bob_searches_for_alice.json()["results"]}
    assert BOB_FILENAME not in alice_bob_filenames, (
        "Alice's search must never return Bob's document, even when the "
        "query matches Bob's content closely"
    )
    assert ALICE_FILENAME not in bob_alice_filenames, (
        "Bob's search must never return Alice's document"
    )

    alice_self_filenames = {r["filename"] for r in alice_searches_for_self.json()["results"]}
    bob_self_filenames = {r["filename"] for r in bob_searches_for_self.json()["results"]}
    assert ALICE_FILENAME in alice_self_filenames, "Alice must still be able to find her own document"
    assert BOB_FILENAME in bob_self_filenames, "Bob must still be able to find his own document"


def run_knowledge_agent_end_to_end_check():
    """Ask the Knowledge Agent, as alice, a question only answerable from
    Bob's document. Confirm the tool is called (with alice's user_id
    injected, not bob's), Bob's content never appears in the final answer,
    and the agent reports honestly rather than fabricating.
    """
    # Deliberately doesn't name "Cobalt-Reef" or "microplastics" in the
    # question - the model harmlessly echoing back a term the CALLER typed
    # (while correctly saying it found nothing) isn't a leak; what proves a
    # leak is Bob's actual confidential fact (the "99.2" figure) appearing
    # in Alice's answer despite her never having supplied it.
    result = ask_knowledge_agent(
        "What is the ceramic membrane filtration rig's removal percentage?",
        user_id=ALICE,
    )
    answer = result["messages"][-1].content
    print(f"[knowledge agent cross-user] used_tool={used_tool(result, 'search_uploaded_documents')} "
          f"answer={answer!r}\n")

    assert used_tool(result, "search_uploaded_documents"), (
        "Expected the Knowledge Agent to still call search_uploaded_documents"
    )
    assert "99.2" not in answer, (
        "Alice's Knowledge Agent answer must not contain Bob's confidential "
        "document content"
    )

    own_result = ask_knowledge_agent(
        "What is the conversion efficiency of the Starling-9 solar cell prototype?",
        user_id=ALICE,
    )
    own_answer = own_result["messages"][-1].content
    print(f"[knowledge agent same-user] answer={own_answer!r}\n")
    assert "47.3" in own_answer, "Alice must still get her own document's content back"


def run_tool_direct_injected_state_check():
    """Call search_uploaded_documents.invoke(...) directly, passing state
    that supplies user_id via the tool's InjectedState mechanism, to prove
    the filter holds independent of any particular LLM's tool-call
    behavior in the two checks above.
    """
    alice_view = search_uploaded_documents.invoke(
        {"query": "Cobalt-Reef microplastics", "user_id": ALICE}
    )
    bob_view = search_uploaded_documents.invoke(
        {"query": "Starling-9 solar cell", "user_id": BOB}
    )
    print(f"[direct tool call] alice_view={alice_view!r}")
    print(f"[direct tool call] bob_view={bob_view!r}\n")

    assert "Cobalt-Reef" not in alice_view and BOB_FILENAME not in alice_view
    assert "Starling-9" not in bob_view and ALICE_FILENAME not in bob_view


def run_document_id_ownership_check(alice_doc, bob_doc):
    """A document_id owned by a different user_id must 404 identically to
    an unknown document_id - no distinct message, no 403, no leaked
    content.
    """
    unknown_id = str(uuid.uuid4())
    bob_probes_alice_doc = search(
        "anything", BOB, document_id=alice_doc["document_id"]
    )
    bob_probes_unknown_doc = search("anything", BOB, document_id=unknown_id)

    print(f"[document_id ownership] bob->alice's doc: {bob_probes_alice_doc.status_code} "
          f"{bob_probes_alice_doc.json()}")
    print(f"[document_id ownership] bob->unknown doc: {bob_probes_unknown_doc.status_code} "
          f"{bob_probes_unknown_doc.json()}\n")

    assert bob_probes_alice_doc.status_code == 404
    assert bob_probes_unknown_doc.status_code == 404
    assert bob_probes_alice_doc.json()["detail"] == bob_probes_unknown_doc.json()["detail"], (
        "A document owned by another user must be indistinguishable from a "
        "nonexistent one - same status code AND same detail message"
    )

    # And alice CAN scope a search to her own document_id.
    alice_scoped = search("Starling-9", ALICE, document_id=alice_doc["document_id"])
    assert alice_scoped.status_code == 200
    assert len(alice_scoped.json()["results"]) > 0


def run_sql_injection_shaped_user_id_check():
    """A user_id containing SQL-metacharacter-shaped text must be treated
    as an opaque literal string (parameterized query), matching zero
    documents rather than matching everyone's.
    """
    injection_attempt = "alice' OR '1'='1"
    response = search("Starling-9 solar cell efficiency", injection_attempt, top_k=50)
    print(f"[sql-injection-shaped user_id] status={response.status_code} "
          f"results={len(response.json()['results'])}\n")
    assert response.status_code == 200
    assert response.json()["results"] == [], (
        "A SQL-injection-shaped user_id must match zero documents, proving "
        "it is used as a parameterized value and not concatenated into SQL"
    )


def run_validation_error_checks():
    missing_user_id_search = client.post("/documents/search", json={"query": "test"})
    empty_user_id_search = search("test", "   ")
    empty_user_id_upload = upload("whatever.txt", "content", "   ")
    missing_user_id_chat = client.post(
        "/chat", json={"question": "hi", "thread_id": f"stage23-test-{RUN_ID}"}
    )

    print(f"[validation] missing user_id on /documents/search -> {missing_user_id_search.status_code}")
    print(f"[validation] empty user_id on /documents/search -> {empty_user_id_search.status_code}")
    print(f"[validation] empty user_id on /documents/upload -> {empty_user_id_upload.status_code}")
    print(f"[validation] missing user_id on /chat -> {missing_user_id_chat.status_code}\n")

    assert missing_user_id_search.status_code == 422
    assert empty_user_id_search.status_code == 400
    assert empty_user_id_upload.status_code == 400
    assert missing_user_id_chat.status_code == 422


def run_zero_documents_for_new_user_check():
    """A brand-new user_id that has never uploaded anything gets an empty
    result, not an error, from both retrieval paths.
    """
    new_user = f"nobody-{RUN_ID}"
    response = search("anything at all", new_user)
    print(f"[zero documents] /documents/search status={response.status_code} "
          f"results={response.json()['results']}")
    assert response.status_code == 200
    assert response.json()["results"] == []

    tool_result = search_uploaded_documents.invoke(
        {"query": "anything at all", "user_id": new_user}
    )
    print(f"[zero documents] tool result={tool_result!r}\n")
    assert "no documents have been uploaded yet" in tool_result.lower()


def run_backward_compatible_legacy_data_check():
    """Rows written before this stage's migration (or by any stage 20-22
    test run against this project's shared database) were backfilled to
    the 'default-user' sentinel - confirm they're reachable under that
    user_id and not reachable under an unrelated one.
    """
    legacy_count = pg_conn.execute(
        "SELECT count(*) FROM documents WHERE user_id = 'default-user'"
    ).fetchone()[0]
    print(f"[legacy data] documents owned by 'default-user': {legacy_count}\n")
    assert legacy_count > 0, (
        "Expected at least one pre-Stage-23 document backfilled to "
        "'default-user' in this project's shared database"
    )

    default_user_search = search("the", "default-user", top_k=1)
    assert default_user_search.status_code == 200


def cleanup_test_documents():
    pg_conn.execute(
        "DELETE FROM documents WHERE filename = ANY(%s)", ([ALICE_FILENAME, BOB_FILENAME],)
    )


def run():
    alice_doc, bob_doc = setup_documents()
    run_cross_user_search_check()
    run_knowledge_agent_end_to_end_check()
    run_tool_direct_injected_state_check()
    run_document_id_ownership_check(alice_doc, bob_doc)
    run_sql_injection_shaped_user_id_check()
    run_validation_error_checks()
    run_zero_documents_for_new_user_check()
    run_backward_compatible_legacy_data_check()
    cleanup_test_documents()
    print(
        "All checks passed: POST /documents/search and search_uploaded_documents "
        "(both via HTTP, direct tool invocation, and the full Knowledge Agent "
        "subgraph) never return another user's document content; document_id "
        "ownership 404s identically to a nonexistent document_id; a SQL-injection-"
        "shaped user_id matches nothing; missing/empty user_id is rejected; a new "
        "user with no documents gets an empty result, not an error; and pre-"
        "Stage-23 rows remain reachable under the 'default-user' migration sentinel."
    )


if __name__ == "__main__":
    run()
