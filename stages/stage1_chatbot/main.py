"""
Stage 1: terminal chatbot with memory.

Core LangGraph concepts introduced here:
- StateGraph: the graph that defines how data (messages) flows between nodes.
- A node: just a function that takes the current state and returns an update.
- A checkpointer: saves state after every step, keyed by thread_id, so the
  same conversation can be resumed across turns.
"""

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

load_dotenv()

llm = ChatOpenAI(model="gpt-4o-mini")


def chatbot(state: MessagesState):
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


graph_builder = StateGraph(MessagesState)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_edge(START, "chatbot")
graph_builder.add_edge("chatbot", END)

graph = graph_builder.compile(checkpointer=MemorySaver())


def main():
    config = {"configurable": {"thread_id": "1"}}
    print("Stage 1 chatbot. Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break

        result = graph.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=config,
        )
        print(f"Bot: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
