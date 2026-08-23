"""
Simple, dependency-free smoke test for Stage 17, the final combined
multi-agent research assistant.

Not a pytest suite (the project has none configured yet) - just a script
that checks four things:

1. The inner supervisor+critic pipeline still behaves exactly like Stage
   16's, now that it's embedded as a helper inside the outer planner:
   routing, a passed verdict, and a retry count within MAX_RETRIES, for one
   question per specialist.
2. The Analysis Agent still actually calls its calculate tool (checked
   against analysis_graph directly, same reasoning as Stage 15/16's own
   tests: the wrapper nodes fold a specialist's result down to its last
   message, dropping intermediate tool-call messages).
3. The retry cap is enforced in code, not just trusted to the critic LLM's
   judgment - same direct critic_node call as Stage 16's test.
4. The OUTER planner + human-approval loop works end to end: a plan is
   produced, approving it (Command(resume="y")) runs research_subtask for
   every subtask (each one delegating to the supervisor+critic pipeline)
   and produces a final_answer; rejecting it (Command(resume="n")) produces
   no final_answer at all.

Run with:
    python stage17_final_multi_agent_system/test_final_multi_agent_system.py
"""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from main import MAX_RETRIES, analysis_graph, critic_node, graph, supervisor_critic_graph

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


def run_supervisor_critic_checks():
    for label, question, expected_route in CASES:
        result = supervisor_critic_graph.invoke(
            {"messages": [{"role": "user", "content": question}]}
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


def run_planner_approval_checks():
    approve_config = {"configurable": {"thread_id": "test-approve"}}
    question = "How does solar power work, and what is the average of 10, 20, and 30?"

    result = graph.invoke({"question": question}, config=approve_config)
    assert "__interrupt__" in result, "Expected the graph to pause for human approval"

    result = graph.invoke(Command(resume="y"), config=approve_config)
    assert "final_answer" in result, "Expected an approved plan to produce a final_answer"
    assert result["final_answer"].strip(), "Expected a non-empty final_answer"
    assert len(result["results"]) == len(result["subtasks"]), (
        "Expected one research result per subtask"
    )

    print("[planner approval check] approved run produced a final answer:")
    print(f"  {result['final_answer']}\n")

    reject_config = {"configurable": {"thread_id": "test-reject"}}
    result = graph.invoke({"question": question}, config=reject_config)
    assert "__interrupt__" in result, "Expected the graph to pause for human approval"

    result = graph.invoke(Command(resume="n"), config=reject_config)
    assert "final_answer" not in result, "Expected a rejected plan to produce no final_answer"

    print("[planner rejection check] rejected run produced no final answer, as expected.\n")


def run():
    run_supervisor_critic_checks()
    run_analysis_tool_check()
    run_retry_cap_check()
    run_planner_approval_checks()
    print(
        "All checks passed: the inner supervisor+critic pipeline still routes and "
        "reviews correctly when embedded as a helper, and the outer planner's "
        "approve/reject loop produces (or withholds) a final synthesized answer."
    )


if __name__ == "__main__":
    run()
