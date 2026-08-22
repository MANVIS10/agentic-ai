"""
Stage 12: two specialist agents, built independently, running side by side.

New concept vs. Stage 1-11:
- Stage 11 showed what ONE specialist agent looks like: Stage 2's
  agent -> tools -> agent loop, narrowed to one tool plus a SystemMessage
  identity. Stage 12 asks what happens when you have TWO of those.
- The answer, at this stage, is: nothing special. Each agent is its own
  complete, self-contained graph - own LLM, own tool, own SystemMessage,
  own MemorySaver, own compiled graph object. Building a second specialist
  doesn't require any new LangGraph mechanism; you just repeat the Stage 11
  pattern with a different tool and a different identity.
- There is NO supervisor, router, or shared state here. The two agents
  don't know about each other and can't hand work off to one another - the
  REPL in main() picks which agent to call based on a prefix you type
  ("research:" or "knowledge:"), which is a plain string check, not agent
  logic. Real routing (an LLM or graph deciding which specialist to use)
  is a future stage.

Research Agent:
- Copied from Stage 11 verbatim: DuckDuckGoSearchRun bound as its one tool,
  identity = "Research Agent" (web research).

Knowledge Agent:
- Built the same way Stage 3 built search_knowledge_base, but wrapped in
  Stage 11's specialist pattern: one tool (local KB retrieval over
  knowledge_base/*.md), identity = "Knowledge Agent" (local documents
  only, not the web).

No new tools were created. No shared `common/` module - both agents'
plumbing is duplicated in this one self-contained file, same as every
other stage.
"""

from pathlib import Path

from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.documents import Document
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()


# ---------------------------------------------------------------------------
# Research Agent - web search specialist (same as Stage 11)
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM_PROMPT = (
    "You are a Research Agent, a specialist whose only job is web research. "
    "You have one tool: web search. When asked something you don't already "
    "know for certain, search the web for it before answering. Report what "
    "you found clearly and cite that it came from a web search when you use "
    "it. Stay focused on research - you're not a general-purpose assistant."
)

search_web = DuckDuckGoSearchRun()
research_tools = [search_web]

research_llm = ChatOpenAI(model="gpt-4o-mini")
research_llm_with_tools = research_llm.bind_tools(research_tools)


def research_agent_node(state: MessagesState):
    messages = [SystemMessage(content=RESEARCH_SYSTEM_PROMPT), *state["messages"]]
    response = research_llm_with_tools.invoke(messages)
    return {"messages": [response]}


research_graph_builder = StateGraph(MessagesState)
research_graph_builder.add_node("agent", research_agent_node)
research_graph_builder.add_node("tools", ToolNode(research_tools))
research_graph_builder.add_edge(START, "agent")
research_graph_builder.add_conditional_edges("agent", tools_condition)
research_graph_builder.add_edge("tools", "agent")

research_graph = research_graph_builder.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Knowledge Agent - local knowledge-base specialist (Stage 3's retrieval,
# wrapped in Stage 11's specialist pattern)
# ---------------------------------------------------------------------------

KNOWLEDGE_SYSTEM_PROMPT = (
    "You are a Knowledge Agent, a specialist whose only job is answering "
    "questions from a local knowledge base of documents. You have one tool: "
    "knowledge-base search. You cannot browse the web or access anything "
    "outside these documents. If the knowledge base doesn't contain the "
    "answer, say so plainly instead of guessing. Stay focused on the "
    "knowledge base - you're not a general-purpose assistant."
)

KNOWLEDGE_BASE_DIR = Path(__file__).parent / "knowledge_base"


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

    Use this when the user asks something that could be answered from the
    project's own documents (currently: renewable energy topics - solar,
    wind, and hydro power) rather than general knowledge or current events.
    """
    results = vector_store.similarity_search(query, k=3)
    if not results:
        return "No relevant information found in the knowledge base."

    formatted = []
    for doc in results:
        source = doc.metadata.get("source", "unknown")
        formatted.append(f"[source: {source}]\n{doc.page_content}")
    return "\n\n".join(formatted)


knowledge_tools = [search_knowledge_base]

knowledge_llm = ChatOpenAI(model="gpt-4o-mini")
knowledge_llm_with_tools = knowledge_llm.bind_tools(knowledge_tools)


def knowledge_agent_node(state: MessagesState):
    messages = [SystemMessage(content=KNOWLEDGE_SYSTEM_PROMPT), *state["messages"]]
    response = knowledge_llm_with_tools.invoke(messages)
    return {"messages": [response]}


knowledge_graph_builder = StateGraph(MessagesState)
knowledge_graph_builder.add_node("agent", knowledge_agent_node)
knowledge_graph_builder.add_node("tools", ToolNode(knowledge_tools))
knowledge_graph_builder.add_edge(START, "agent")
knowledge_graph_builder.add_conditional_edges("agent", tools_condition)
knowledge_graph_builder.add_edge("tools", "agent")

knowledge_graph = knowledge_graph_builder.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# REPL - a plain prefix check picks which independent agent answers.
# This is NOT routing logic: no agent decides anything here, it's just
# string matching in main() before either graph is ever invoked.
# ---------------------------------------------------------------------------

def main():
    research_config = {"configurable": {"thread_id": "research-1"}}
    knowledge_config = {"configurable": {"thread_id": "knowledge-1"}}

    print("Stage 12: two independent specialist agents.")
    print("Type 'research: <question>' or 'knowledge: <question>'.")
    print("Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break

        if user_input.lower().startswith("research:"):
            question = user_input.split(":", 1)[1].strip()
            result = research_graph.invoke(
                {"messages": [{"role": "user", "content": question}]},
                config=research_config,
            )
            print(f"[Research Agent]: {result['messages'][-1].content}\n")

        elif user_input.lower().startswith("knowledge:"):
            question = user_input.split(":", 1)[1].strip()
            result = knowledge_graph.invoke(
                {"messages": [{"role": "user", "content": question}]},
                config=knowledge_config,
            )
            print(f"[Knowledge Agent]: {result['messages'][-1].content}\n")

        else:
            print("Please prefix your question with 'research:' or 'knowledge:'\n")


if __name__ == "__main__":
    main()
