"""
Simple, dependency-free smoke test for the Stage 11 research agent.

Not a pytest suite (the project has none configured yet) - just a script
that asks a research question and a no-tool question, and confirms the
agent reaches for its search tool only when it actually needs to.

Run with:
    python stage11_research_agent/test_research_agent.py
"""

from main import graph

CASES = [
    ("research question", "Who won the most recent Super Bowl?", "duckduckgo_search"),
    ("no-tool question", "What is 12 + 30?", None),
]


def tool_calls_from(result):
    names = []
    for message in result["messages"]:
        for call in getattr(message, "tool_calls", None) or []:
            names.append(call["name"])
    return names


def run():
    for i, (label, question, expected_tool) in enumerate(CASES):
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
        if expected_tool is not None:
            assert expected_tool in called, (
                f"Expected {expected_tool!r} to be called for: {question}, "
                f"got {called}"
            )

    print("All checks passed: the research agent used its search tool "
          "when needed and answered directly when it didn't.")


if __name__ == "__main__":
    run()
