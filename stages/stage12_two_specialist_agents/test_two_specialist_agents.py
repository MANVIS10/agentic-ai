"""
Simple, dependency-free smoke test for the Stage 12 specialist agents.

Not a pytest suite (the project has none configured yet) - just a script
that asks each agent a question suited to its own tool, and confirms each
one only reaches for the tool it actually has: the Research Agent doesn't
know about the knowledge base, and the Knowledge Agent doesn't know about
the web.

Run with:
    python stage12_two_specialist_agents/test_two_specialist_agents.py
"""

from main import knowledge_graph, research_graph

CASES = [
    ("Research Agent", research_graph, "Who won the most recent Super Bowl?", "duckduckgo_search"),
    ("Knowledge Agent", knowledge_graph, "How does solar power generate electricity?", "search_knowledge_base"),
]


def tool_calls_from(result):
    names = []
    for message in result["messages"]:
        for call in getattr(message, "tool_calls", None) or []:
            names.append(call["name"])
    return names


def run():
    for i, (label, graph, question, expected_tool) in enumerate(CASES):
        config = {"configurable": {"thread_id": f"test-{i}"}}
        result = graph.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
        )

        called = tool_calls_from(result)
        answer = result["messages"][-1].content

        print(f"[{label}] Q: {question}")
        print(f"  tool(s) called: {called or 'none'}")
        print(f"  answer: {answer}\n")

        assert answer.strip(), f"Expected a non-empty answer for: {question}"
        assert expected_tool in called, (
            f"Expected {expected_tool!r} to be called for: {question}, got {called}"
        )
        other_tool = "search_knowledge_base" if expected_tool == "duckduckgo_search" else "duckduckgo_search"
        assert other_tool not in called, (
            f"{label} should never call {other_tool!r} - it doesn't have that tool"
        )

    print("All checks passed: each specialist agent used only its own tool.")


if __name__ == "__main__":
    run()
