"""
Stage 10: one agent, four tools - the LLM picks whichever one fits the
question, with no planner or subtask breakdown involved.

New concept vs. Stage 1-9:
- Nothing structurally new in LangGraph terms - this is Stage 2's exact
  `agent -> tools -> agent` shape (bind_tools, ToolNode, tools_condition,
  MemorySaver, REPL loop). The only change is binding all four tools from
  Stages 2-5 instead of one.
- This isolates *tool selection* on its own. Stage 8 also bound all four
  tools together, but buried that agent one level down as a node inside a
  bigger plan -> approve -> research -> synthesize graph. Here the
  four-tool agent IS the whole graph - you ask a question, it picks a
  tool (or none) and answers directly.

No new tools were created - the four tools below are duplicated verbatim
from Stages 2-5 (self-contained stages, no shared `common/` module).
"""

from pathlib import Path

from dotenv import load_dotenv
import io

import requests
from bs4 import BeautifulSoup
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pypdf import PdfReader

load_dotenv()

MAX_CHARS = 4000
KNOWLEDGE_BASE_DIR = Path(__file__).parent / "knowledge_base"

# --- Tools (verbatim from Stages 2-5) ---------------------------------

search_web = DuckDuckGoSearchRun()


def load_knowledge_base() -> list[Document]:
    """Read every .md file in knowledge_base/ and split it into chunks."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)
    documents = []
    for path in sorted(KNOWLEDGE_BASE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for chunk in splitter.split_text(text):
            documents.append(Document(page_content=chunk, metadata={"source": path.name}))
    return documents


embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vector_store = InMemoryVectorStore(embeddings)
vector_store.add_documents(load_knowledge_base())


@tool
def search_knowledge_base(query: str) -> str:
    """Search the local knowledge base for information relevant to a
    natural-language question and return the most relevant text chunks.

    Use this when the question could be answered from the project's own
    documents (currently: renewable energy topics - solar, wind, hydro)
    rather than general knowledge or current events.
    """
    results = vector_store.similarity_search(query, k=3)
    if not results:
        return "No relevant information found in the knowledge base."

    formatted = []
    for doc in results:
        source = doc.metadata.get("source", "unknown")
        formatted.append(f"[source: {source}]\n{doc.page_content}")
    return "\n\n".join(formatted)


@tool
def fetch_webpage(url: str) -> str:
    """Fetch a webpage by URL and return its readable text content.

    Use this when given a specific URL and asked what's on that page, or to
    summarize/read a webpage.
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


@tool
def fetch_pdf(url: str) -> str:
    """Download a PDF by URL and return its extracted text content.

    Use this when given a URL that points to a PDF file and asked what's in
    it, or to summarize/read a PDF.
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


tools = [search_web, search_knowledge_base, fetch_webpage, fetch_pdf]

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
    print("Stage 10 multi-tool agent. Type 'exit' to quit.\n")

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
