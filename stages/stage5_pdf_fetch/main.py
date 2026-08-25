"""
Stage 5: chatbot that can fetch a PDF by URL and read its text content.

New concept vs. Stage 4:
- The fetched content is binary (a PDF file), not HTML text. It has to be
  downloaded first, then parsed with a PDF-specific library (`pypdf`) to
  pull the text out — BeautifulSoup (Stage 4) only understands markup.

Everything else (state, nodes, conditional edges, ReAct loop, memory) is
the same shape as Stage 2/3/4 — only the tool changes.
"""

import io

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pypdf import PdfReader

load_dotenv()

MAX_CHARS = 4000


@tool
def fetch_pdf(url: str) -> str:
    """Download a PDF by URL and return its extracted text content.

    Use this when the user gives you a URL that points to a PDF file and
    wants to know what's in it, or asks you to summarize/read a PDF.
    """
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException as e:
        return f"Failed to fetch {url}: {e}"

    try:
        reader = PdfReader(io.BytesIO(response.content))
        text = " ".join(
            " ".join((page.extract_text() or "").split()) for page in reader.pages
        )
    except Exception as e:
        return f"Failed to read PDF at {url}: {e}"

    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "...[truncated]"

    return text


tools = [fetch_pdf]

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
    print("Stage 5 pdf-fetch agent. Type 'exit' to quit.\n")

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
