"""
Simple, dependency-free smoke test for the Stage 16 three-specialist
supervisor.

Not a pytest suite (the project has none configured yet) - just a script
that checks three things:

1. End-to-end routing: one question per specialist (research, knowledge,
   analysis), asserting the supervisor's routing decision (state["next"])
   directly rather than guessing it from the final answer's wording, plus
   a non-empty answer, a passed critic verdict, and a retry count within
   MAX_RETRIES.
2. The Analysis Agent actually calls its calculate tool. The outer
   supervisor graph's wrapper nodes fold each specialist's subgraph result
   down to just its last message, so intermediate tool-call messages never
   reach the outer graph's state - this is checked by invoking the
   analysis_graph subgraph directly instead, the same way Stage 15's own
   test does.
3. The retry cap itself: critic_node is called directly with a hand-built
   state where retry_count has already reached MAX_RETRIES. Since a real
   "inadequate answer" isn't something you can reliably provoke from the
   critic LLM on demand, this proves the cap is actually enforced in code
   (forces "pass" instead of trusting the LLM's own judgment), not just
   present in theory.

Run with:
    python stage16_three_specialist_supervisor/test_three_specialist_supervisor.py
"""

from langchain_core.messages import AIMessage, HumanMessage

from main import MAX_RETRIES, analysis_graph, critic_node, graph

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
    (
        "numerical question",
        "What is the average of 12, 18, and 30?",
        "analysis",
    ),
]


def tool_calls_from(result):
    names = []
    for message in result["messages"]:
        for call in getattr(message, "tool_calls", None) or []:
            names.append(call["name"])
    return names


def run_routing_checks():
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


def run_analysis_tool_check():
    question = "What is the average of 12, 18, and 30?"
    result = analysis_graph.invoke({"messages": [{"role": "user", "content": question}]})

    called = tool_calls_from(result)
    answer = result["messages"][-1].content

    print(f"[analysis tool check] Q: {question}")
    print(f"  tool(s) called: {called or 'none'}")
    print(f"  answer: {answer}\n")

    assert "calculate" in called, (
        f"Expected the Analysis Agent to call 'calculate' for: {question}, got {called}"
    )
    assert "20" in answer, f"Expected '20' to appear in the answer, got: {answer!r}"


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
    run_routing_checks()
    run_analysis_tool_check()
    run_retry_cap_check()
    print(
        "All checks passed: the supervisor routed each question to the correct "
        "specialist (including analysis), the analysis specialist used its "
        "calculator, and the retry cap held."
    )


if __name__ == "__main__":
    run()
