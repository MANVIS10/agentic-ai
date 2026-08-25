"""
Dependency-free smoke test for Stage 19 - Stage 18's graph, now driven
through a FastAPI HTTP API instead of direct graph.invoke() calls.

Not a pytest suite (the project has none configured yet) - just a script
that checks, through HTTP requests via FastAPI's TestClient, the same kind
of things Stage 17/18's tests check directly against the graph:

1. GET /health reports the API and its Postgres dependency are up.
2. POST /chat on a fresh thread_id produces a plan and pauses for approval.
3. Running two different questions back-to-back on the SAME thread_id
   (approve the first, reject the second) doesn't leak the first
   question's final_answer into the second - the same regression Stage
   17/18 test directly, now exercised over HTTP.
4. POST /approve produces a non-empty final_answer with one result per
   subtask; POST /reject produces no final_answer at all.
5. Error responses are correct: /approve on a thread_id that was never
   started returns 404; /approve on a thread_id that already completed
   returns 409; /chat with a missing required field returns 422.
6. Concurrency: two requests racing on the SAME thread_id no longer
   produce the wrong answer. This covers the bug where /approve, fired
   before an in-flight /chat's checkpoint had committed, read a STALE
   prior checkpoint and returned an incorrect 409 - and confirms a
   duplicate simultaneous /approve now resolves deterministically (one
   200, one legitimate 409) instead of racily.

Like Stage 18's test, checkpoint rows for this test's fixed thread_ids
persist in Postgres across script runs, so run() deletes any old rows for
them first to keep repeated manual runs deterministic.

Run with:
    python stage19_fastapi_backend/test_fastapi_backend.py
"""

import threading
import time

from fastapi.testclient import TestClient

from main import app, checkpointer, graph

client = TestClient(app)

TEST_THREAD_IDS = [
    "test-chat",
    "test-same-thread",
    "test-approve",
    "test-reject",
    "test-race-chat-approve",
    "test-race-duplicate-approve-0",
    "test-race-duplicate-approve-1",
    "test-race-duplicate-approve-2",
    "test-race-duplicate-chat",
]


def clear_previous_test_threads():
    """Delete any checkpoint rows this test's hardcoded thread_ids left
    behind in a previous run - PostgresSaver keeps rows across process
    restarts, unlike Stage 17's MemorySaver.
    """
    for thread_id in TEST_THREAD_IDS:
        checkpointer.delete_thread(thread_id)


def run_health_check():
    response = client.get("/health")
    print(f"[health check] status={response.status_code} body={response.json()}\n")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert response.json()["status"] == "ok", "Expected status 'ok' from /health"


def run_normal_chat_check():
    response = client.post(
        "/chat",
        json={
            "question": "How does solar power work, and what is the average of 10, 20, and 30?",
            "thread_id": "test-chat",
        },
    )
    body = response.json()

    print(f"[normal chat check] status={response.status_code}")
    print(f"  subtasks: {body.get('subtasks')}")
    print(f"  approval_prompt: {body.get('approval_prompt')!r}\n")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert body["status"] == "awaiting_approval", (
        f"Expected status 'awaiting_approval', got {body['status']!r}"
    )
    assert body["subtasks"], "Expected a non-empty plan"
    assert body["approval_prompt"], "Expected a non-empty approval prompt"


def run_same_thread_multi_question_check():
    """Regression check: two different questions run back-to-back on the
    SAME thread_id. Approve the first, reject the second, and confirm the
    second result doesn't still carry the first question's final_answer -
    exactly what Stage 17/18's test checks directly against graph.invoke(),
    now driven entirely through /chat, /approve, and /reject.
    """
    thread_id = "test-same-thread"

    first_question = "How does solar power generate electricity?"
    response = client.post("/chat", json={"question": first_question, "thread_id": thread_id})
    assert response.json()["status"] == "awaiting_approval"

    response = client.post("/approve", json={"thread_id": thread_id})
    first_answer = response.json().get("final_answer", "").strip()
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert first_answer, "Expected the first (approved) question to produce a final_answer"

    second_question = "What is the average of 12, 18, and 30?"
    response = client.post("/chat", json={"question": second_question, "thread_id": thread_id})
    assert response.json()["status"] == "awaiting_approval"

    response = client.post("/reject", json={"thread_id": thread_id})
    second_body = response.json()
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert not second_body.get("final_answer"), (
        "Expected the second (rejected) question to produce no final_answer, but got "
        f"a leftover answer from the first question: {second_body.get('final_answer')!r}"
    )

    print(
        "[same-thread multi-question check] rejecting a second question on the same "
        "thread did not leak the first question's final_answer, as expected.\n"
    )


def run_approval_check():
    thread_id = "test-approve"
    question = "How does solar power work, and what is the average of 10, 20, and 30?"

    response = client.post("/chat", json={"question": question, "thread_id": thread_id})
    assert response.json()["status"] == "awaiting_approval"

    response = client.post("/approve", json={"thread_id": thread_id})
    body = response.json()

    print(f"[approval check] status={response.status_code}")
    print(f"  final_answer: {body.get('final_answer')}\n")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert body["status"] == "completed", f"Expected status 'completed', got {body['status']!r}"
    assert body.get("final_answer", "").strip(), (
        "Expected an approved plan to produce a non-empty final_answer"
    )
    assert len(body["results"]) == len(body["subtasks"]), (
        "Expected one research result per subtask"
    )


def run_rejection_check():
    thread_id = "test-reject"
    question = "How does solar power work, and what is the average of 10, 20, and 30?"

    response = client.post("/chat", json={"question": question, "thread_id": thread_id})
    assert response.json()["status"] == "awaiting_approval"

    response = client.post("/reject", json={"thread_id": thread_id})
    body = response.json()

    print(f"[rejection check] status={response.status_code}, final_answer={body.get('final_answer')!r}\n")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert body["status"] == "rejected", f"Expected status 'rejected', got {body['status']!r}"
    assert not body.get("final_answer"), "Expected a rejected plan to produce no final_answer"


def run_race_chat_vs_approve_check():
    """Reproduces the originally reported bug: /approve fired before an
    in-flight /chat's checkpoint has committed used to read a STALE prior
    checkpoint for a reused thread_id and return an incorrect 409, even
    though /chat itself went on to return "awaiting_approval" moments
    later. Uses "test-approve", which already has completed history from
    run_approval_check() above - exactly the condition needed to turn the
    race into a 409 instead of a 404.

    After the fix, /approve should BLOCK until /chat's checkpoint commits,
    then correctly resume it - not race ahead and misfire.
    """
    thread_id = "test-approve"
    question = "How does wind power work?"
    outcome = {}

    def do_chat():
        t0 = time.time()
        response = client.post("/chat", json={"question": question, "thread_id": thread_id})
        outcome["chat"] = (response.status_code, response.json().get("status"), time.time() - t0)

    def do_approve():
        time.sleep(0.1)  # fire while /chat's plan() LLM call is still in flight
        t0 = time.time()
        response = client.post("/approve", json={"thread_id": thread_id})
        outcome["approve"] = (response.status_code, response.json(), time.time() - t0)

    chat_thread = threading.Thread(target=do_chat)
    approve_thread = threading.Thread(target=do_approve)
    chat_thread.start()
    approve_thread.start()
    chat_thread.join()
    approve_thread.join()

    chat_status, chat_body_status, chat_elapsed = outcome["chat"]
    approve_status, approve_body, approve_elapsed = outcome["approve"]

    print(
        f"[race: chat vs approve] chat -> {chat_status} {chat_body_status!r} "
        f"({chat_elapsed:.2f}s), approve -> {approve_status} ({approve_elapsed:.2f}s)\n"
    )

    assert chat_status == 200 and chat_body_status == "awaiting_approval", (
        f"Expected /chat to pause for approval, got {chat_status} {chat_body_status!r}"
    )
    # The old bug: approve_status == 409 here, returned almost instantly
    # (a stale read), well before /chat's slow LLM call even finished.
    assert approve_status == 200, (
        f"Expected /approve to wait for /chat's checkpoint and then succeed, "
        f"got {approve_status}: {approve_body}"
    )
    assert approve_body.get("status") == "completed", (
        f"Expected status 'completed', got {approve_body.get('status')!r}"
    )
    assert approve_elapsed >= chat_elapsed - 0.5, (
        "Expected /approve to have BLOCKED until /chat's checkpoint committed, not "
        f"returned early (approve took {approve_elapsed:.2f}s, chat took {chat_elapsed:.2f}s)"
    )


def run_duplicate_approve_check():
    """Two /approve calls fired concurrently on the same thread_id must
    resolve deterministically: exactly one succeeds (200, "completed",
    one result per subtask - proving the research only ran once, not
    twice), and the other gets a legitimate 409 (not a race-dependent
    coin flip, and not both succeeding).
    """
    for i in range(3):
        thread_id = f"test-race-duplicate-approve-{i}"
        question = "What is the average of 4, 8, and 12?"

        response = client.post("/chat", json={"question": question, "thread_id": thread_id})
        assert response.json()["status"] == "awaiting_approval"

        outcomes = [None, None]

        def do_approve(slot):
            r = client.post("/approve", json={"thread_id": thread_id})
            outcomes[slot] = (r.status_code, r.json())

        t0 = threading.Thread(target=do_approve, args=(0,))
        t1 = threading.Thread(target=do_approve, args=(1,))
        t0.start()
        t1.start()
        t0.join()
        t1.join()

        statuses = sorted(status for status, _ in outcomes)
        print(f"[duplicate approve #{i}] statuses={statuses}")

        assert statuses == [200, 409], (
            f"Expected exactly one 200 and one 409, got {statuses}"
        )

        winner = next(body for status, body in outcomes if status == 200)
        assert winner.get("final_answer", "").strip(), "Expected the winning approve to produce a final_answer"
        assert len(winner["results"]) == len(winner["subtasks"]), (
            "Expected exactly one research result per subtask - a duplicate approve "
            "must not run research_subtask twice"
        )
    print()


def run_duplicate_chat_check():
    """Two /chat calls fired concurrently on the same (fresh) thread_id
    should not corrupt the checkpoint chain - both should complete cleanly,
    and the thread should end up in one coherent paused state.
    """
    thread_id = "test-race-duplicate-chat"
    outcomes = [None, None]

    def do_chat(slot, question):
        r = client.post("/chat", json={"question": question, "thread_id": thread_id})
        outcomes[slot] = (r.status_code, r.json().get("status"))

    t0 = threading.Thread(target=do_chat, args=(0, "How does hydropower work?"))
    t1 = threading.Thread(target=do_chat, args=(1, "How does wind power work?"))
    t0.start()
    t1.start()
    t0.join()
    t1.join()

    print(f"[duplicate chat] outcomes={outcomes}\n")

    for status, body_status in outcomes:
        assert status == 200 and body_status == "awaiting_approval", (
            f"Expected both concurrent /chat calls to pause cleanly, got {outcomes}"
        )

    state = graph.get_state({"configurable": {"thread_id": thread_id}})
    assert state.next == ("human_approval",), (
        f"Expected one coherent paused state after concurrent /chat calls, got next={state.next}"
    )


def run_error_case_checks():
    # /approve on a thread_id that was never started at all.
    response = client.post("/approve", json={"thread_id": "test-nonexistent"})
    print(f"[error case: unknown thread] status={response.status_code}")
    assert response.status_code == 404, f"Expected 404, got {response.status_code}"

    # /approve again on a thread_id that already completed above.
    response = client.post("/approve", json={"thread_id": "test-approve"})
    print(f"[error case: already-completed thread] status={response.status_code}")
    assert response.status_code == 409, f"Expected 409, got {response.status_code}"

    # /chat with a required field missing.
    response = client.post("/chat", json={"question": "no thread_id here"})
    print(f"[error case: missing thread_id field] status={response.status_code}\n")
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"


def run():
    clear_previous_test_threads()
    run_health_check()
    run_normal_chat_check()
    run_same_thread_multi_question_check()
    run_approval_check()
    run_rejection_check()
    run_race_chat_vs_approve_check()
    run_duplicate_approve_check()
    run_duplicate_chat_check()
    run_error_case_checks()
    print(
        "All checks passed: /health, /chat, /approve, and /reject behave correctly "
        "over HTTP, state doesn't leak across questions on a shared thread_id, "
        "concurrent requests on the same thread_id resolve deterministically instead "
        "of racing, and unknown/already-settled/malformed requests get the right "
        "error responses."
    )


if __name__ == "__main__":
    run()
