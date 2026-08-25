"""
Stage 6: planner that breaks a research question into subtasks, answers
each one in turn, then combines the answers into a final response.

New concept vs. Stage 1-5:
- Every earlier stage used `MessagesState` + one LLM node + a tool +
  `tools_condition`, so the "loop" was really the ReAct tool-call loop
  LangGraph gives you for free. This stage has no tool at all. The state
  is a plain plan (question, subtasks, current progress, results), and
  the loop is a conditional edge *we* write by hand: "is there another
  subtask left? go research it. otherwise, move on and synthesize."

Everything else (StateGraph, add_edge, add_conditional_edges, compile) is
the same LangGraph machinery as every previous stage - just pointed at a
different shape of state.
"""

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

load_dotenv()

llm = ChatOpenAI(model="gpt-4o-mini")


class PlannerState(TypedDict):
    question: str
    subtasks: list[str]
    current_index: int
    results: list[str]
    final_answer: str


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
graph_builder.add_node("research_subtask", research_subtask)
graph_builder.add_node("synthesize", synthesize)

graph_builder.add_edge(START, "plan")
graph_builder.add_edge("plan", "research_subtask")
graph_builder.add_conditional_edges("research_subtask", has_more_subtasks)
graph_builder.add_edge("synthesize", END)

graph = graph_builder.compile()


def main():
    print("Stage 6 planner agent. Type 'exit' to quit.\n")

    while True:
        question = input("Research question: ").strip()
        if question.lower() in {"exit", "quit"}:
            break

        result = graph.invoke({"question": question})
        print(f"\nFinal answer: {result['final_answer']}\n")


if __name__ == "__main__":
    main()
