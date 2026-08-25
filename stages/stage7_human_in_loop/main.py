"""
Stage 7: same planner as Stage 6, but the plan is now shown to a human and
must be approved before any research happens.

New concept vs. Stage 1-6:
- `interrupt()`: called from inside a node, it pauses the graph mid-run and
  hands a value back to the caller instead of returning normally. The graph
  is not done - it's parked at that node, waiting.
- `Command(resume=...)`: how the caller "wakes the graph back up," feeding
  in the human's answer and continuing execution from exactly where it
  paused - same thread, same state, no replaying earlier steps.
- This only works with a checkpointer (`MemorySaver`, already used since
  Stage 1) - it's what lets the graph "remember" it was paused mid-node for
  a specific thread_id and resume in place instead of starting over.

Everything else - `PlannerState`, `plan`, `research_subtask`,
`has_more_subtasks`, `synthesize` - is exactly Stage 6's planner, unchanged.
The only addition is one node, `human_approval`, sitting between "the plan
is ready" and "research begins." Reject routes straight to END - no
research happens at all.
"""

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

load_dotenv()

llm = ChatOpenAI(model="gpt-4o-mini")


class PlannerState(TypedDict):
    question: str
    subtasks: list[str]
    current_index: int
    results: list[str]
    final_answer: str
    approved: bool


def plan(state: PlannerState):
    prompt = (
        "Break the following research question into 2-3 short, concrete "
        "subtasks that could each be researched independently. "
        "Reply with just the subtasks, one per line, no numbering.\n\n"
        f"Question: {state['question']}"
    )
    response = llm.invoke(prompt)
    subtasks = [line.strip() for line in response.content.splitlines() if line.strip()]

    print(f"\nPlan ({len(subtasks)} subtasks):")
    for i, subtask in enumerate(subtasks, start=1):
        print(f"  {i}. {subtask}")

    return {"subtasks": subtasks, "current_index": 0, "results": []}


def human_approval(state: PlannerState):
    decision = interrupt("Approve this plan? (y/n): ")
    return {"approved": decision.strip().lower() == "y"}


def route_after_approval(state: PlannerState) -> str:
    return "research_subtask" if state["approved"] else END


def research_subtask(state: PlannerState):
    subtask = state["subtasks"][state["current_index"]]
    print(f"\nResearching: {subtask}")

    response = llm.invoke(subtask)

    return {
        "results": state["results"] + [response.content],
        "current_index": state["current_index"] + 1,
    }


def has_more_subtasks(state: PlannerState) -> str:
    if state["current_index"] < len(state["subtasks"]):
        return "research_subtask"
    return "synthesize"


def synthesize(state: PlannerState):
    subtasks_and_results = "\n\n".join(
        f"Subtask: {subtask}\nAnswer: {result}"
        for subtask, result in zip(state["subtasks"], state["results"])
    )
    prompt = (
        f"Original question: {state['question']}\n\n"
        f"Research notes:\n{subtasks_and_results}\n\n"
        "Combine these into one clear final answer to the original question."
    )
    response = llm.invoke(prompt)
    return {"final_answer": response.content}


graph_builder = StateGraph(PlannerState)
graph_builder.add_node("plan", plan)
graph_builder.add_node("human_approval", human_approval)
graph_builder.add_node("research_subtask", research_subtask)
graph_builder.add_node("synthesize", synthesize)

graph_builder.add_edge(START, "plan")
graph_builder.add_edge("plan", "human_approval")
graph_builder.add_conditional_edges("human_approval", route_after_approval)
graph_builder.add_conditional_edges("research_subtask", has_more_subtasks)
graph_builder.add_edge("synthesize", END)

graph = graph_builder.compile(checkpointer=MemorySaver())


def run_until_settled(initial_input, config):
    """Invoke the graph, pausing on each interrupt to ask the user y/n."""
    result = graph.invoke(initial_input, config=config)

    while "__interrupt__" in result:
        prompt = result["__interrupt__"][0].value
        answer = input(f"{prompt} ").strip()
        result = graph.invoke(Command(resume=answer), config=config)

    return result


def main():
    config = {"configurable": {"thread_id": "1"}}
    print("Stage 7 human-in-the-loop planner. Type 'exit' to quit.\n")

    while True:
        question = input("Research question: ").strip()
        if question.lower() in {"exit", "quit"}:
            break

        result = run_until_settled({"question": question}, config)

        if "final_answer" in result:
            print(f"\nFinal answer: {result['final_answer']}\n")
        else:
            print("\nPlan was not approved - no research was done.\n")


if __name__ == "__main__":
    main()
