"""
Simple, dependency-free smoke test for the Stage 15 analysis agent.

Not a pytest suite (the project has none configured yet) - just a script
that asks a handful of numeric/comparison questions and confirms the agent
reaches for its calculate tool when arithmetic is needed, and gives a
sensible answer either way.

Run with:
    python stage15_analysis_agent/test_analysis_agent.py
"""

from main import graph

CASES = [
    ("average", "What is the average of 12, 18, and 30?", "calculate", "20"),
    (
        "percentage increase",
        "A value went from 100 to 120. What is the percentage increase?",
        "calculate",
        "20",
    ),
    (
        "comparison",
        "Category A sold 340 units and Category B sold 275 units. "
        "Which category sold more?",
        None,
        "A",
    ),
    (
        "in-scope, no tool needed",
        "Between the numbers 45 and 62, which one is bigger?",
        None,
        "62",
    ),
]


def tool_calls_from(result):
    names = []
    for message in result["messages"]:
        for call in getattr(message, "tool_calls", None) or []:
            names.append(call["name"])
    return names


def run():
    for i, (label, question, expected_tool, expected_substring) in enumerate(CASES):
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
        assert expected_substring.lower() in answer.lower(), (
            f"Expected {expected_substring!r} to appear in the answer for: "
            f"{question}, got: {answer!r}"
        )

    print("All checks passed: the analysis agent used its calculate tool "
          "for arithmetic and reasoned correctly about the results.")


if __name__ == "__main__":
    run()
