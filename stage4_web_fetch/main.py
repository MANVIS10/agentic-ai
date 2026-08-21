"""
Stage 4: chatbot that can fetch a webpage and read its text content.

New concept vs. Stage 2/3:
- The tool has a side effect that reaches outside the process (an HTTP
  request to a URL the model chooses), instead of searching the web
  (Stage 2) or a local vector store (Stage 3).
- Turning a messy HTML response into plain text the LLM can actually read
  is part of the tool itself, not something LangChain does for us.

Everything else (state, nodes, conditional edges, ReAct loop, memory) is
the same shape as Stage 2/3 — only the tool changes.
"""

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()

MAX_CHARS = 4000


@tool
def fetch_webpage(url: str) -> str:
    """Fetch a webpage by URL and return its readable text content.

    Use this when the user gives you a specific URL and wants to know
    what's on that page, or asks you to summarize/read a webpage.
    """
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException as e:
        return f"Failed to fetch {url}: {e}"

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = " ".join(soup.get_text(separator=" ").split())
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "...[truncated]"

    return text


tools = [fetch_webpage]

llm = ChatOpenAI(model="gpt-4o-mini")
llm_with_tools = llm.bind_tools(tools)


def agent(state: MessagesState):
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}


graph_builder = StateGraph(MessagesState)
graph_builder.add_node("agent", agent)
graph_builder.add_node("tools", ToolNode(tools))

graph_builder.add_edge(START, "agent")
graph_builder.add_conditional_edges("agent", tools_condition)
graph_builder.add_edge("tools", "agent")

graph = graph_builder.compile(checkpointer=MemorySaver())


def main():
    config = {"configurable": {"thread_id": "1"}}
    print("Stage 4 web-fetch agent. Type 'exit' to quit.\n")

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
