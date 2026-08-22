"""
Simple, dependency-free test proving long-term memory survives across
separate interactions.

Not a pytest suite (the project has none configured yet) - just a script
that calls save_memory/load_memory directly and asserts a fact saved in
one call is still there in a later, independent call. Uses its own temp
file so it never touches a real long_term_memory.json.

Run with:
    python stage9_simple_memory/test_simple_memory.py
"""

from pathlib import Path

from main import load_memory, save_memory

TEST_FILE = Path(__file__).parent / "test_long_term_memory.json"


def run():
    if TEST_FILE.exists():
        TEST_FILE.unlink()

    # Nothing saved yet - simulates a fresh recall before any remember.
    assert load_memory(TEST_FILE) is None, "Expected no memory before saving."
    print("Before saving: load_memory() correctly returned None.")

    # "One interaction": the user asks the bot to remember something.
    fact = "the user's favorite color is teal"
    save_memory(fact, TEST_FILE)
    print(f"Saved: {fact!r}")

    # "A later interaction": a separate load_memory call, standing in for
    # a new conversation turn (or even a new thread_id / process).
    recalled = load_memory(TEST_FILE)
    print(f"Recalled: {recalled!r}")

    assert recalled == fact, (
        f"Expected recalled fact to equal saved fact, got {recalled!r}"
    )

    TEST_FILE.unlink()
    print("All checks passed: a fact saved in one interaction was "
          "retrieved correctly in a later interaction.")


if __name__ == "__main__":
    run()
