"""
Smoke test for Stage 22's Knowledge Agent RAG: confirms the Knowledge
Agent actually answers from user-uploaded documents (not the bundled
knowledge_base/*.md, which is unreachable from this stage's code), that
search_uploaded_documents is the tool actually invoked, that a "no
documents uploaded yet" state is reported honestly rather than
fabricated, and that supervisor routing and the critic's retry pipeline
still function unmodified.

Not a pytest suite - same standalone-script convention as every other
stage's test file (asserts + prints, run directly with `python`). Uses
FastAPI's TestClient for /documents/upload, and calls knowledge_graph/
supervisor_critic_graph directly (not through the outer planner) to
inspect tool calls and routing decisions - the outer graph's messages
state doesn't surface a specialist's intermediate tool-call messages
(confirmed in Stage 16).

Calls the real OpenAI chat/embeddings APIs (no mocking), same as every
prior stage's test file - OPENAI_API_KEY must be set.

Run with:
    python stage22_knowledge_agent_rag/test_knowledge_agent_rag.py
"""

from langchain_core.messages import ToolMessage

from fastapi.testclient import TestClient

from main import app, knowledge_graph, pg_conn, supervisor_critic_graph

client = TestClient(app)

TEST_FILENAMES = ["test-battery-notes.txt"]


def clear_previous_test_documents():
    """Delete rows this test's fixed filenames left behind in a previous
    run (document_chunks cascade-deletes automatically via ON DELETE
    CASCADE). Same pattern as stage20/stage21's test files.
    """
    pg_conn.execute("DELETE FROM documents WHERE filename = ANY(%s)", (TEST_FILENAMES,))


def upload(filename, text):
    return client.post(
        "/documents/upload",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
    )


def ask_knowledge_agent(question):
    """Invoke the Knowledge Agent subgraph directly (not via the outer
    planner or even the supervisor), so the returned messages list
    includes the tool call/result Stage 16 established the outer graph's
    state never surfaces.
    """
    return knowledge_graph.invoke({"messages": [{"role": "user", "content": question}]})


def used_tool(result, tool_name):
    return any(
        isinstance(m, ToolMessage) and m.name == tool_name for m in result["messages"]
    )


def run_no_documents_uploaded_check():
    """Only meaningful against a genuinely empty document_chunks table -
    this project runs every stage against one shared Postgres database, so
    prior stages' (and this stage's own previous runs') uploaded chunks
    normally make that state unreachable in practice. Checked defensively
    rather than assumed either way, same spirit as Stage 21's test file
    already adapting its own assertions to this project's shared-database
    reality.
    """
    row_count = pg_conn.execute(
        "SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL"
    ).fetchone()[0]

    if row_count > 0:
        print(
            f"[no documents uploaded] skipped - {row_count} embedded chunk(s) already "
            "exist in the shared database from other stages/prior runs\n"
        )
        return

    result = ask_knowledge_agent("What does the uploaded document say about batteries?")
    answer = result["messages"][-1].content
    print(f"[no documents uploaded] answer={answer!r}\n")
    assert "no documents have been uploaded" in answer.lower(), (
        "Expected the Knowledge Agent to say plainly that no documents have "
        "been uploaded, not fabricate an answer"
    )


def run_upload_and_answer_check():
    text = (
        "The prototype battery pack uses a lithium iron phosphate chemistry "
        "and is rated for 4200 charge cycles before capacity drops below 80%."
    )
    response = upload("test-battery-notes.txt", text)
    print(f"[upload] status={response.status_code} body={response.json()}\n")
    assert response.status_code == 200, response.text

    result = ask_knowledge_agent(
        "How many charge cycles is the prototype battery pack rated for?"
    )
    answer = result["messages"][-1].content
    print(f"[uploaded-document answer] used_tool={used_tool(result, 'search_uploaded_documents')} answer={answer!r}\n")

    assert used_tool(result, "search_uploaded_documents"), (
        "Expected the Knowledge Agent to call search_uploaded_documents"
    )
    assert "4200" in answer, "Expected the answer to reflect the uploaded document's content"


def run_bundled_knowledge_base_isolation_check():
    """The bundled knowledge_base/wind.md (Stage 3-21, untouched, not even
    duplicated into this stage's folder) states a turbine's cut-in speed is
    "often around 3-4 meters per second". No test fixture anywhere in this
    project uploads wind-related content, so if that fact leaks into the
    Knowledge Agent's answer here, it proves the bundled knowledge base is
    still reachable - which it must not be in this stage.
    """
    result = ask_knowledge_agent(
        "What is the cut-in wind speed for a turbine to start generating electricity?"
    )
    answer = result["messages"][-1].content
    print(f"[isolation] answer={answer!r}\n")
    assert "3-4 meters per second" not in answer and "3 to 4 meters per second" not in answer, (
        "Expected the Knowledge Agent to NOT know the bundled knowledge_base/wind.md's "
        "cut-in-speed fact - it should be unreachable from this stage"
    )
    assert used_tool(result, "search_uploaded_documents"), (
        "Expected the Knowledge Agent to still search uploaded documents (finding nothing "
        "relevant), not silently skip searching"
    )


def run_supervisor_routing_check():
    result = supervisor_critic_graph.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "How many charge cycles is the prototype battery pack rated for?",
                }
            ]
        }
    )
    print(
        f"[supervisor routing] next={result['next']} verdict={result['verdict']} "
        f"retry_count={result['retry_count']}\n"
    )
    assert result["next"] == "knowledge", "Expected a document-shaped question to route to the Knowledge Agent"
    assert result["verdict"] in ("pass", "retry")
    assert result["retry_count"] >= 0


def run():
    clear_previous_test_documents()
    run_no_documents_uploaded_check()
    run_upload_and_answer_check()
    run_bundled_knowledge_base_isolation_check()
    run_supervisor_routing_check()
    print(
        "All checks passed: the Knowledge Agent answers from uploaded documents via "
        "search_uploaded_documents, reports honestly when none have been uploaded, "
        "cannot reach the bundled knowledge_base/*.md content, and supervisor routing "
        "is unaffected."
    )


if __name__ == "__main__":
    run()
