"""
Simple, dependency-free smoke test for the Stage 14 critic.

Not a pytest suite (the project has none configured yet) - just a script
that checks two things:

1. End-to-end: one real question per specialist, asserting a non-empty final
   answer and that the retry counter never exceeds MAX_RETRIES.
2. The retry cap itself: critic_node is called directly with a hand-built
   state where retry_count has already reached MAX_RETRIES. Since a real
   "inadequate answer" isn't something you can reliably provoke from the
   critic LLM on demand, this proves the cap is actually enforced in code
   (forces "pass" instead of trusting the LLM's own judgment), not just
   present in theory.

Run with:
    python stage14_critic/test_critic.py
"""

from langchain_core.messages import AIMessage, HumanMessage

from main import MAX_RETRIES, critic_node

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


def run_end_to_end():
    from main import graph

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
        print(f"  critic verdict: {result['verdict']!r}, retries used: {result['retry_count']}")
        print(f"  answer: {answer}\n")

        assert actual_route == expected_route, (
            f"Expected supervisor to route {label!r} to {expected_route!r}, "
            f"got {actual_route!r}"
        )
        assert answer.strip(), f"Expected a non-empty answer for: {question}"
        assert result["retry_count"] <= MAX_RETRIES, (
            f"retry_count {result['retry_count']} exceeded MAX_RETRIES {MAX_RETRIES}"
        )
        assert result["verdict"] == "pass", (
            "Expected the graph to only ever end with a passed verdict, "
            f"got {result['verdict']!r}"
        )


def run_retry_cap_check():
    state = {
        "messages": [
            HumanMessage(content="How does solar power generate electricity?"),
            AIMessage(content="I don't know."),
        ],
        "next": "knowledge",
        "verdict": "pass",
        "feedback": "",
        "retry_count": MAX_RETRIES,
    }
    review = critic_node(state)

    print(f"[retry cap check] retry_count already at MAX_RETRIES ({MAX_RETRIES})")
    print(f"  critic_node returned: {review}\n")

    assert review["verdict"] == "pass", (
        "Expected critic_node to force a pass once retries are exhausted, "
        f"got {review['verdict']!r}"
    )


def run():
    run_end_to_end()
    run_retry_cap_check()
    print("All checks passed: critic reviewed each answer and the retry cap held.")


if __name__ == "__main__":
    run()
