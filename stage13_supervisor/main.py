"""
Stage 13: supervisor agent - routes a question to one of two specialists.

New concept vs. Stage 1-12:
- Stage 12 built two complete, independent specialist agents (Research Agent,
  Knowledge Agent) but had no way to pick between them except a hard-coded
  string prefix ("research:" / "knowledge:") typed by the human in main().
  That's not routing - it's the human doing the routing.
- Stage 13 replaces the prefix check with a supervisor NODE: a plain LLM
  call (no tools) that reads the user's question and decides which
  specialist should answer it. The decision comes back as STRUCTURED
  output - a small typed object ({"next": "research"} or
  {"next": "knowledge"}) - instead of free-form text we'd have to parse
  with string matching or regex. A LangGraph conditional edge then reads
  that field and sends the message to the matching specialist node.

Architecture:

    START -> supervisor -> (conditional edge on state["next"])
                                |-> research_agent -> END
                                |-> knowledge_agent -> END

Both specialists are copied from Stage 12 with no changes to their own
agent -> tools -> agent loop. The only new pieces are the supervisor node,
the shared state's "next" field, and the conditional edge that reads it.
No new tools, no critic, no retries, no agent-to-agent communication - the
supervisor picks once per question and the chosen specialist's answer is
the final answer.
"""

from pathlib import Path
from typing import Literal

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
from typing_extensions import TypedDict

load_dotenv()


# ---------------------------------------------------------------------------
# Research Agent - web search specialist (copied from Stage 12, unchanged)
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


research_subgraph_builder = StateGraph(MessagesState)
research_subgraph_builder.add_node("agent", research_agent_node)
research_subgraph_builder.add_node("tools", ToolNode(research_tools))
research_subgraph_builder.add_edge(START, "agent")
research_subgraph_builder.add_conditional_edges("agent", tools_condition)
research_subgraph_builder.add_edge("tools", "agent")

research_graph = research_subgraph_builder.compile()


# ---------------------------------------------------------------------------
# Knowledge Agent - local knowledge-base specialist (copied from Stage 12,
# unchanged, using this folder's own knowledge_base/ copy)
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


knowledge_subgraph_builder = StateGraph(MessagesState)
knowledge_subgraph_builder.add_node("agent", knowledge_agent_node)
knowledge_subgraph_builder.add_node("tools", ToolNode(knowledge_tools))
knowledge_subgraph_builder.add_edge(START, "agent")
knowledge_subgraph_builder.add_conditional_edges("agent", tools_condition)
knowledge_subgraph_builder.add_edge("tools", "agent")

knowledge_graph = knowledge_subgraph_builder.compile()


# ---------------------------------------------------------------------------
# Supervisor - the new concept in this stage.
#
# Shared state: MessagesState's "messages" plus a "next" field that holds
# the supervisor's routing decision so both the conditional edge and any
# caller (e.g. the test) can see which specialist was picked.
#
# Structured output: instead of asking the LLM to write a sentence like
# "I think this should go to the research agent" and then parsing that
# text, we give it a small typed schema (Route) and call
# with_structured_output(Route). The LLM's reply comes back already as a
# dict shaped like {"next": "research"} or {"next": "knowledge"} - nothing
# to parse, and it can only ever be one of those two literal values.
# ---------------------------------------------------------------------------


class SupervisorState(MessagesState):
    next: Literal["research", "knowledge"]


class Route(TypedDict):
    """The supervisor's routing decision."""

    next: Literal["research", "knowledge"]


SUPERVISOR_SYSTEM_PROMPT = (
    "You are a supervisor that routes a user's question to exactly one of "
    "two specialist agents:\n\n"
    "- 'research': a Research Agent that searches the live web. Use this "
    "for current events, recent news, or anything that changes over time "
    "and needs up-to-date information.\n"
    "- 'knowledge': a Knowledge Agent that searches a local knowledge base "
    "covering renewable energy topics (solar, wind, and hydro power). Use "
    "this for questions about those topics.\n\n"
    "Read the user's latest question and decide which one specialist "
    "should handle it."
)

# method="function_calling" (a real tool call) instead of the default
# "json_schema": as the conversation grows across turns, gpt-4o-mini's
# default structured-output mode occasionally echoes back the JSON SCHEMA
# itself (e.g. {"type": "object", "properties": {"next": "knowledge"}})
# instead of an instance of it, which crashes route["next"] with a
# KeyError. function_calling reliably returns just {"next": ...}.
supervisor_llm = ChatOpenAI(model="gpt-4o-mini").with_structured_output(
    Route, method="function_calling"
)


def supervisor_node(state: SupervisorState):
    messages = [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT), *state["messages"]]
    route: Route = supervisor_llm.invoke(messages)
    return {"next": route["next"]}


def research_node(state: SupervisorState):
    """Run the Research Agent subgraph and hand back only its final answer."""
    result = research_graph.invoke({"messages": state["messages"]})
    return {"messages": [result["messages"][-1]]}


def knowledge_node(state: SupervisorState):
    """Run the Knowledge Agent subgraph and hand back only its final answer."""
    result = knowledge_graph.invoke({"messages": state["messages"]})
    return {"messages": [result["messages"][-1]]}


def route_from_supervisor(state: SupervisorState) -> str:
    return state["next"]


supervisor_graph_builder = StateGraph(SupervisorState)
supervisor_graph_builder.add_node("supervisor", supervisor_node)
supervisor_graph_builder.add_node("research_agent", research_node)
supervisor_graph_builder.add_node("knowledge_agent", knowledge_node)

supervisor_graph_builder.add_edge(START, "supervisor")
supervisor_graph_builder.add_conditional_edges(
    "supervisor",
    route_from_supervisor,
    {"research": "research_agent", "knowledge": "knowledge_agent"},
)
supervisor_graph_builder.add_edge("research_agent", END)
supervisor_graph_builder.add_edge("knowledge_agent", END)

graph = supervisor_graph_builder.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# REPL - one graph, no prefixes. The supervisor decides for you.
# ---------------------------------------------------------------------------

def main():
    config = {"configurable": {"thread_id": "supervisor-1"}}

    print("Stage 13: supervisor routes your question to a specialist.")
    print("Just ask - no prefix needed. Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        try:
            result = graph.invoke(
                {"messages": [{"role": "user", "content": user_input}]},
                config=config,
            )
        except Exception as exc:
            # A specialist's tool call can hit a transient failure (e.g. a
            # network error in the web search tool). Without this, that
            # exception would propagate out of main() and kill the whole
            # REPL process, ending the session for every question after it -
            # not because of any corrupted graph state, just an unhandled
            # error from one turn.
            print(f"[Error] This question could not be answered: {exc}\n")
            continue

        print(f"[Supervisor routed to: {result['next']}]")
        print(f"[{result['next'].capitalize()} Agent]: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
