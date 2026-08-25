"""
Simple, dependency-free smoke test for the Stage 8 research workflow.

Not a pytest suite (the project has none configured yet) - just a script
that runs the full graph end-to-end on a fixed question, auto-approves the
plan (skipping the interactive y/n prompt), and asserts a final answer
comes out the other end.

Run with:
    python stage8_research_workflow/test_research_workflow.py
"""

from langgraph.types import Command

from main import graph

QUESTION = "What is solar power and how does it compare to wind power?"


def run():
    config = {"configurable": {"thread_id": "test"}}

    result = graph.invoke({"question": QUESTION}, config=config)
    assert "__interrupt__" in result, "Expected the graph to pause for approval."

    print(f"Plan:\n{result['subtasks']}\n")

    result = graph.invoke(Command(resume="y"), config=config)

    assert "final_answer" in result, "Expected a final_answer after approval."
    assert len(result["results"]) == len(result["subtasks"]), (
        "Expected one research result per subtask."
    )
    assert result["final_answer"].strip(), "final_answer should not be empty."

    print(f"Final answer:\n{result['final_answer']}")
    print("\nAll checks passed: plan -> approval -> tool-using research -> "
          "synthesis produced a final answer.")


if __name__ == "__main__":
    run()
