"""
Dependency-free smoke test for Stage 18 - Stage 17's final multi-agent
system, now checkpointed to PostgreSQL instead of MemorySaver.

Not a pytest suite (the project has none configured yet) - just a script
that checks the same five things Stage 17's test does, since the whole
graph is byte-identical - only the checkpointer backend changed:

1. The inner supervisor+critic pipeline still behaves exactly like
   Stage 16/17's: routing, a passed verdict, and a retry count within
   MAX_RETRIES, for one question per specialist.
2. The Analysis Agent still actually calls its calculate tool.
3. The retry cap is enforced in code, not just trusted to the critic LLM's
   judgment.
4. The OUTER planner + human-approval loop works end to end against the
   real Postgres-backed `graph`: a plan is produced, approving it
   (Command(resume="y")) runs research_subtask for every subtask and
   produces a final_answer; rejecting it (Command(resume="n")) produces no
   final_answer at all.
5. Running two different questions back-to-back on the SAME thread_id
   doesn't leak state between them.

Unlike Stage 17, checkpoint rows for this test's thread_ids persist in
Postgres across script runs (MemorySaver reset to empty every process;
PostgresSaver does not) - so run() deletes any old rows for those thread_ids
first, to keep repeated manual runs deterministic. This also doubles as a
demonstration of `checkpointer.delete_thread`, a capability that only makes
sense for a persistent backend.

Run with:
    python stage18_postgres_persistence/test_postgres_persistence.py
"""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from main import (
    MAX_RETRIES,
    analysis_graph,
    checkpointer,
    critic_node,
    graph,
    supervisor_critic_graph,
)

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

TEST_THREAD_IDS = ["test-approve", "test-reject", "test-same-thread"]


def tool_calls_from(result):
    names = []
    for message in result["messages"]:
        for call in getattr(message, "tool_calls", None) or []:
            names.append(call["name"])
    return names


def clear_previous_test_threads():
    """Delete any checkpoint rows this test's hardcoded thread_ids left
    behind in a previous run, so repeated manual runs stay deterministic -
    unlike Stage 17's MemorySaver, PostgresSaver keeps rows across process
    restarts.
    """
    for thread_id in TEST_THREAD_IDS:
        checkpointer.delete_thread(thread_id)


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
    assert result.get("final_answer", "").strip(), (
        "Expected an approved plan to produce a non-empty final_answer"
    )
    assert len(result["results"]) == len(result["subtasks"]), (
        "Expected one research result per subtask"
    )

    print("[planner approval check] approved run produced a final answer:")
    print(f"  {result['final_answer']}\n")

    reject_config = {"configurable": {"thread_id": "test-reject"}}
    result = graph.invoke({"question": question}, config=reject_config)
    assert "__interrupt__" in result, "Expected the graph to pause for human approval"

    result = graph.invoke(Command(resume="n"), config=reject_config)
    assert not result.get("final_answer"), "Expected a rejected plan to produce no final_answer"

    print("[planner rejection check] rejected run produced no final answer, as expected.\n")


def run_same_thread_multi_question_check():
    """Regression test for the stale-final_answer bug: two different
    questions run back-to-back on the SAME thread_id (unlike every other
    check above, which gives each scenario its own thread). Approve the
    first question, then reject the second, and confirm the second result
    doesn't still carry the first question's final_answer - which is
    exactly what happened when plan() didn't reset final_answer/approved
    and main() reused one thread_id for a whole REPL session. Still holds
    with a persistent checkpointer: plan() always runs first on any
    START-rooted invoke and always resets these fields, regardless of what
    the backing store remembers.
    """
    config = {"configurable": {"thread_id": "test-same-thread"}}

    first_question = "How does solar power generate electricity?"
    result = graph.invoke({"question": first_question}, config=config)
    assert "__interrupt__" in result, "Expected the graph to pause for human approval"
    result = graph.invoke(Command(resume="y"), config=config)
    first_answer = result.get("final_answer", "").strip()
    assert first_answer, "Expected the first (approved) question to produce a final_answer"

    second_question = "What is the average of 12, 18, and 30?"
    result = graph.invoke({"question": second_question}, config=config)
    assert "__interrupt__" in result, "Expected the graph to pause for human approval"
    result = graph.invoke(Command(resume="n"), config=config)
    assert not result.get("final_answer"), (
        "Expected the second (rejected) question to produce no final_answer, but got "
        f"a leftover answer from the first question: {result.get('final_answer')!r}"
    )

    print(
        "[same-thread multi-question check] rejecting a second question on the same "
        "thread did not leak the first question's final_answer, as expected.\n"
    )


def run():
    clear_previous_test_threads()
    run_supervisor_critic_checks()
    run_analysis_tool_check()
    run_retry_cap_check()
    run_planner_approval_checks()
    run_same_thread_multi_question_check()
    print(
        "All checks passed: the inner supervisor+critic pipeline still routes and "
        "reviews correctly when embedded as a helper, and the outer planner's "
        "approve/reject loop produces (or withholds) a final synthesized answer - now "
        "checkpointed to PostgreSQL instead of MemorySaver."
    )


if __name__ == "__main__":
    run()
