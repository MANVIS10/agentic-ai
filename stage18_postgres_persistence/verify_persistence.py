"""
Manual demo: prove Stage 18's checkpoints survive a Python process restart.

main()'s REPL generates a fresh uuid.uuid4() thread_id per question, on
purpose (see main.py), so there's no fixed thread_id a human could type into
a second REPL invocation to resume interactively. This script uses one
FIXED thread_id ("persistence-demo") instead, specifically so a restart can
be demonstrated:

  1. First run (no arguments): starts a question, prints the plan and the
     human-approval prompt, then exits immediately WITHOUT resuming -
     simulating a crash while a human was mid-decision. Nothing after the
     interrupt has happened yet.
  2. Kill/close this process. Optionally also restart the Postgres
     container (`docker compose restart postgres`, or
     `docker compose down && docker compose up -d` without `-v`) to prove
     checkpoints survive a container restart too, not just a Python one.
  3. Second run, with --check: makes NO graph.invoke() call at all. It only
     calls graph.get_state(config) - a pure read - and prints the recovered
     state (the plan/subtasks it produced, and confirmation execution is
     still parked before human_approval). That the plan is still there,
     recovered from Postgres with zero LLM calls or graph execution in
     between, is the proof persistence worked.

Usage:
    python stage18_postgres_persistence/verify_persistence.py
    (kill the process after the approval prompt appears)
    python stage18_postgres_persistence/verify_persistence.py --check
"""

import sys

from main import graph

THREAD_ID = "persistence-demo"


def start():
    config = {"configurable": {"thread_id": THREAD_ID}}
    question = "How does solar power work, and what is the average of 10, 20, and 30?"

    print(f"Starting a run on thread_id={THREAD_ID!r} ...\n")
    result = graph.invoke({"question": question}, config=config)

    if "__interrupt__" in result:
        prompt = result["__interrupt__"][0].value
        print(f"Graph paused: {prompt}")
        print(
            "\nNot resuming - exiting now to simulate a crash. Kill this "
            "process (it's about to exit on its own anyway), then run this "
            "script again with --check to prove the plan above survived."
        )
    else:
        print("Graph did not pause as expected - nothing to demonstrate.")


def check():
    config = {"configurable": {"thread_id": THREAD_ID}}

    print(f"Reading checkpointed state for thread_id={THREAD_ID!r} (no graph.invoke() call) ...\n")
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
        "restart."
    )


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        start()
