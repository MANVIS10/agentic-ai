"""
Stage 8: Stage 7's plan -> human approval -> research loop -> synthesize,
but each subtask is now researched by a small tool-calling agent instead of
a bare `llm.invoke(subtask)`.

New concept vs. Stage 1-7:
- Nothing structurally new in LangGraph terms. This stage is about
  *composition*: the inner research agent is exactly the `agent -> tools ->
  agent` graph every stage from 2-5 already built (bind_tools, ToolNode,
  tools_condition), just with all four of their tools bound together
  instead of one each. It's invoked like a function from inside the outer
  planner's `research_subtask` node, so the LLM picks whichever tool (web
  search, knowledge base, webpage fetch, PDF fetch) actually fits each
  subtask, instead of answering from training data alone.
- The outer planner graph (`PlannerState`, `plan`, `human_approval`,
  `has_more_subtasks`, `synthesize`, interrupt/resume) is exactly Stage 7,
  unchanged.

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
from langgraph.types import Command, interrupt
from pypdf import PdfReader
from typing_extensions import TypedDict

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


# --- Inner research agent (Stage 2-5's agent -> tools -> agent shape) -

def agent(state: MessagesState):
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}


research_agent_builder = StateGraph(MessagesState)
research_agent_builder.add_node("agent", agent)
research_agent_builder.add_node("tools", ToolNode(tools))

research_agent_builder.add_edge(START, "agent")
research_agent_builder.add_conditional_edges("agent", tools_condition)
research_agent_builder.add_edge("tools", "agent")

research_agent = research_agent_builder.compile()


# --- Outer planner graph (Stage 7, unchanged) --------------------------

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

    result = research_agent.invoke({"messages": [{"role": "user", "content": subtask}]})
    answer = result["messages"][-1].content

    return {
        "results": state["results"] + [answer],
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
    print("Stage 8 research workflow. Type 'exit' to quit.\n")

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
