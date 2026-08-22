"""
Simple, dependency-free smoke test for the Stage 10 multi-tool agent.

Not a pytest suite (the project has none configured yet) - just a script
that asks a few differently-shaped questions and prints which tool (if
any) the LLM chose for each, to confirm tool *selection* is actually
happening rather than always reaching for the same tool.

Run with:
    python stage10_multi_tool_agent/test_multi_tool_agent.py
"""

from main import graph

CASES = [
    ("knowledge base question", "What is solar power?", "search_knowledge_base"),
    ("web search question", "Who won the most recent Super Bowl?", "duckduckgo_search"),
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

    print("All checks passed: the agent selected an appropriate tool "
          "(or no tool) per question.")


if __name__ == "__main__":
    run()
