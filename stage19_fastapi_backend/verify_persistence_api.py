"""
Manual demo: prove Stage 19's checkpoints survive a Python process restart,
same as Stage 18's verify_persistence.py - but driven through the FastAPI
app instead of calling graph.invoke()/graph.get_state() directly.

Uses one FIXED thread_id ("persistence-demo-api") so a restart can be
demonstrated across two separate script invocations:

  1. First run (no arguments): imports `app` (which, at import time,
     constructs a fresh pg_conn/checkpointer/graph exactly like starting
     the real API with uvicorn would), sends it a POST /chat through
     TestClient, and prints the plan + approval prompt - then exits
     immediately WITHOUT approving, simulating a crash while a human was
     mid-decision.
  2. Kill/close this process. Optionally also restart the Postgres
     container (`docker compose restart postgres`) to prove checkpoints
     survive a container restart too, not just a Python one.
  3. Second run, with --check: a genuinely separate `python` invocation, so
     `main.py` is re-imported from scratch - a brand-new pg_conn,
     checkpointer, graph, and app, all reading the same Postgres database
     via DATABASE_URL. It first calls graph.get_state(config) directly (no
     HTTP, no TestClient) to prove the state survived independent of any
     route, then sends POST /approve through a freshly-built TestClient and
     confirms it produces a final_answer - proving a freshly constructed
     FastAPI app in a NEW process can resume a thread it never itself
     started.

Note: TestClient talks to the ASGI `app` object in-process rather than over
a real socket, but since each `python verify_persistence_api.py [...]`
invocation is its own OS process with its own fresh import of main.py, the
"fresh app/graph bound to the same Postgres DB" property still genuinely
holds. Running a real `uvicorn` server plus a `requests`-based script is the
alternative that would exercise a real socket too, but isn't implemented
here, to avoid adding a second HTTP client dependency for a demo script.

Usage:
    python stage19_fastapi_backend/verify_persistence_api.py
    (kill the process after the approval prompt appears)
    python stage19_fastapi_backend/verify_persistence_api.py --check
"""

import sys

from fastapi.testclient import TestClient

from main import app, graph

THREAD_ID = "persistence-demo-api"


def start():
    client = TestClient(app)
    question = "How does solar power work, and what is the average of 10, 20, and 30?"

    print(f"Starting a run on thread_id={THREAD_ID!r} via POST /chat ...\n")
    response = client.post("/chat", json={"question": question, "thread_id": THREAD_ID})
    body = response.json()

    if body.get("status") == "awaiting_approval":
        print(f"API paused: {body['approval_prompt']}")
        print(f"Plan: {body['subtasks']}")
        print(
            "\nNot approving - exiting now to simulate a crash. Kill this "
            "process (it's about to exit on its own anyway), then run this "
            "script again with --check to prove the plan above survived."
        )
    else:
        print(f"Unexpected response, nothing to demonstrate: {body}")


def check():
    config = {"configurable": {"thread_id": THREAD_ID}}

    print(f"Reading checkpointed state for thread_id={THREAD_ID!r} (no HTTP call yet) ...\n")
    state = graph.get_state(config)

    if not state.values:
        print(
            "No checkpointed state found. Run this script without --check "
            "first to create one."
        )
        return

    print(f"Recovered question: {state.values.get('question')!r}")
    print(f"Recovered subtasks: {state.values.get('subtasks')}")
    print(f"Still paused before: {state.next}")
    print(
        "\nThis state was read straight from Postgres with no LLM call and "
        "no graph execution in between - proof it survived the process "
        "restart, in a NEW Python process that imported main.py fresh.\n"
    )

    print("Now resuming via POST /approve through a freshly-built TestClient ...\n")
    client = TestClient(app)
    response = client.post("/approve", json={"thread_id": THREAD_ID})
    body = response.json()

    print(f"status={response.status_code}")
    print(f"final_answer: {body.get('final_answer')}")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert body.get("final_answer", "").strip(), (
        "Expected the resumed thread to produce a non-empty final_answer"
    )
    print(
        "\nConfirmed: a freshly constructed FastAPI app in a new process "
        "resumed a thread it never itself started."
    )


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        start()
