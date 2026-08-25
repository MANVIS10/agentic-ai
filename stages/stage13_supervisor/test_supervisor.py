"""
Simple, dependency-free smoke test for the Stage 13 supervisor.

Not a pytest suite (the project has none configured yet) - just a script
that asks two questions, one suited to each specialist, and checks the
routing decision directly (state["next"]) rather than guessing it from the
final answer's wording. It also confirms the routed specialist actually
produced a non-empty answer.

Run with:
    python stage13_supervisor/test_supervisor.py
"""

from main import graph

CASES = [
    (
        "current-events question",
        "What is the latest news about SpaceX launches this week?",
        "research",
    ),
    (
        "knowledge-base question",
        "How does solar power generate electricity?",
        "knowledge",
    ),
]


def run():
    for i, (label, question, expected_route) in enumerate(CASES):
        config = {"configurable": {"thread_id": f"test-{i}"}}
        result = graph.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
        )

        actual_route = result["next"]
        answer = result["messages"][-1].content

        print(f"[{label}] Q: {question}")
        print(f"  supervisor routed to: {actual_route!r}")
        print(f"  answer: {answer}\n")

        assert actual_route == expected_route, (
            f"Expected supervisor to route {label!r} to {expected_route!r}, "
            f"got {actual_route!r}"
        )
        assert answer.strip(), f"Expected a non-empty answer for: {question}"

    print("All checks passed: supervisor routed each question to the correct specialist.")


if __name__ == "__main__":
    run()
