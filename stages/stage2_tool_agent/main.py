"""
Stage 2: chatbot that can use a tool (web search) to answer questions
it doesn't already know the answer to.

New concepts vs. Stage 1:
- Binding tools to the LLM, so it can *decide* to call one instead of
  answering directly.
- ToolNode: a prebuilt node that actually executes whichever tool the LLM
  asked for.
- Conditional edges: the graph now branches, instead of being a straight
  line. After the chatbot responds, we check "did it ask for a tool?" and
  route accordingly.
- This branch + loop is what's usually called the ReAct pattern
  (Reason -> Act -> observe result -> Reason again -> ... -> final answer).
"""

from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()

search_tool = DuckDuckGoSearchRun()
tools = [search_tool]

llm = ChatOpenAI(model="gpt-4o-mini")
llm_with_tools = llm.bind_tools(tools)


def chatbot(state: MessagesState):
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}


graph_builder = StateGraph(MessagesState)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", ToolNode(tools))

graph_builder.add_edge(START, "chatbot")
graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")

graph = graph_builder.compile(checkpointer=MemorySaver())


def main():
    config = {"configurable": {"thread_id": "1"}}
    print("Stage 2 tool agent. Type 'exit' to quit.\n")

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
