"""
Stage 14: critic/reviewer - checks a specialist's answer before it's final,
and can send it back for one retry to the SAME specialist if it isn't good
enough.

New concept vs. Stage 1-13:
- Stage 13 added a supervisor that routes a question to one of two
  specialists and returns whatever that specialist says as the final
  answer, unreviewed - right or wrong, that's what the user gets.
- Stage 14 adds one more node after the specialist: a CRITIC that reads the
  original question and the specialist's answer and judges whether it's
  adequate. If yes, the answer becomes final. If no, the critic writes a
  short note about what's wrong and sends the question back to the SAME
  specialist (not the supervisor - the routing decision doesn't change) for
  one more attempt, this time with that feedback attached. A retry counter,
  bumped inside the critic node itself, caps this at one retry so the loop
  can never run forever.

Architecture:

    START -> supervisor -> (conditional edge on state["next"])
                                |-> research_agent  -\
                                |-> knowledge_agent -/-> critic
                                                          |
                                            (conditional edge on state["verdict"])
                                                |-> "pass"  -> END
                                                |-> "retry" -> back to the
                                                               SAME specialist
                                                               (state["next"])

Both specialists and the supervisor are copied from Stage 13 with no changes
to their own agent -> tools -> agent loop or routing logic. The only new
pieces are the critic node, the "verdict"/"feedback"/"retry_count" fields in
state, the conditional edge after critic, and the one-line addition to each
specialist node that attaches the critic's feedback on a retry.
"""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

load_dotenv()

MAX_RETRIES = 1  # at most one retry per question - two specialist attempts total


# ---------------------------------------------------------------------------
# Research Agent - web search specialist (copied from Stage 13, unchanged)
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
# Knowledge Agent - local knowledge-base specialist (copied from Stage 13,
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
# Supervisor - routing decision (copied from Stage 13, unchanged except one
# extra field: "retry_count": 0, resetting the retry counter for each new
# question since the supervisor runs exactly once per question, before any
# retries happen).
# ---------------------------------------------------------------------------


class CriticState(MessagesState):
    next: Literal["research", "knowledge"]
    verdict: Literal["pass", "retry"]
    feedback: str
    retry_count: int


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
# itself instead of an instance of it, which crashes a dict lookup with a
# KeyError. function_calling reliably returns just the typed fields.
supervisor_llm = ChatOpenAI(model="gpt-4o-mini").with_structured_output(
    Route, method="function_calling"
)


def supervisor_node(state: CriticState):
    messages = [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT), *state["messages"]]
    route: Route = supervisor_llm.invoke(messages)
    return {"next": route["next"], "retry_count": 0}


def research_node(state: CriticState):
    """Run the Research Agent subgraph and hand back only its final answer.

    On a retry, the critic's feedback (state["feedback"]) is attached as an
    extra message so this attempt actually differs from the first one -
    the specialist's previous answer is already in state["messages"] from
    the first attempt, so this just adds the reviewer's note on top of it.
    """
    messages = state["messages"]
    if state.get("feedback"):
        messages = messages + [
            HumanMessage(
                content=f"Reviewer feedback: {state['feedback']} "
                "Please address this and try again."
            )
        ]
    result = research_graph.invoke({"messages": messages})
    return {"messages": [result["messages"][-1]]}


def knowledge_node(state: CriticState):
    """Run the Knowledge Agent subgraph and hand back only its final answer.

    Same feedback-on-retry addition as research_node above.
    """
    messages = state["messages"]
    if state.get("feedback"):
        messages = messages + [
            HumanMessage(
                content=f"Reviewer feedback: {state['feedback']} "
                "Please address this and try again."
            )
        ]
    result = knowledge_graph.invoke({"messages": messages})
    return {"messages": [result["messages"][-1]]}


def route_from_supervisor(state: CriticState) -> str:
    return state["next"]


# ---------------------------------------------------------------------------
# Critic - the new concept in this stage.
#
# A plain LLM call (no tools) that reads the original question and the
# specialist's latest answer and decides whether it's adequate. Like the
# supervisor, the decision comes back as STRUCTURED output instead of free
# text to parse - here a small typed object with a verdict and, on retry, a
# short note about what's wrong.
#
# The retry cap (MAX_RETRIES) is enforced right here: if retries are already
# exhausted, critic_node forces a "pass" regardless of what the critic LLM
# says, so the graph can never loop forever. That keeps the conditional edge
# after critic simple - it only ever has to read state["verdict"].
# ---------------------------------------------------------------------------


class Review(TypedDict):
    """The critic's judgment of a specialist's answer."""

    verdict: Literal["pass", "retry"]
    feedback: str


CRITIC_SYSTEM_PROMPT = (
    "You are a critic reviewing whether an answer adequately addresses a "
    "user's question. Pass if the answer is relevant, reasonably complete, "
    "and not a refusal or empty non-answer. Retry if it clearly misses the "
    "question, is empty, or is far too vague to be useful. If you retry, "
    "give one short sentence of feedback describing what's wrong; leave "
    "feedback empty if you pass."
)

# Same method="function_calling" fix as the supervisor's structured output,
# for the same reliability reason.
critic_llm = ChatOpenAI(model="gpt-4o-mini").with_structured_output(
    Review, method="function_calling"
)


def critic_node(state: CriticState):
    question = state["messages"][0].content
    answer = state["messages"][-1].content
    messages = [
        SystemMessage(content=CRITIC_SYSTEM_PROMPT),
        HumanMessage(content=f"Question: {question}\n\nAnswer: {answer}"),
    ]
    review: Review = critic_llm.invoke(messages)

    if review["verdict"] == "retry" and state["retry_count"] < MAX_RETRIES:
        return {
            "verdict": "retry",
            "feedback": review["feedback"],
            "retry_count": state["retry_count"] + 1,
        }
    return {"verdict": "pass", "feedback": ""}


def route_from_critic(state: CriticState) -> str:
    if state["verdict"] == "pass":
        return "end"
    return state["next"]


supervisor_graph_builder = StateGraph(CriticState)
supervisor_graph_builder.add_node("supervisor", supervisor_node)
supervisor_graph_builder.add_node("research_agent", research_node)
supervisor_graph_builder.add_node("knowledge_agent", knowledge_node)
supervisor_graph_builder.add_node("critic", critic_node)

supervisor_graph_builder.add_edge(START, "supervisor")
supervisor_graph_builder.add_conditional_edges(
    "supervisor",
    route_from_supervisor,
    {"research": "research_agent", "knowledge": "knowledge_agent"},
)
supervisor_graph_builder.add_edge("research_agent", "critic")
supervisor_graph_builder.add_edge("knowledge_agent", "critic")
supervisor_graph_builder.add_conditional_edges(
    "critic",
    route_from_critic,
    {"research": "research_agent", "knowledge": "knowledge_agent", "end": END},
)

graph = supervisor_graph_builder.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# REPL - one graph, no prefixes. The supervisor routes, the critic reviews.
# ---------------------------------------------------------------------------

def main():
    config = {"configurable": {"thread_id": "critic-1"}}

    print("Stage 14: supervisor routes, critic reviews before the answer is final.")
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
        if result["retry_count"] > 0:
            print(f"[Critic asked for {result['retry_count']} retry(ies) before passing]")
        print(f"[Critic: {result['verdict']}]")
        print(f"[{result['next'].capitalize()} Agent]: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
