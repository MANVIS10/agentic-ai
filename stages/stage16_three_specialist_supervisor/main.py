"""
Stage 16: three-specialist supervisor - extends Stage 14's supervisor+critic
graph to route to a third specialist, the Analysis Agent.

New concept vs. Stage 1-15:
- Stage 15 built a third independent specialist (Analysis Agent, a
  `calculate` tool for safe arithmetic) but wired it up standalone, with no
  supervisor - the same way Stage 11 (Research) and Stage 12 (Knowledge)
  were built before Stage 13 added routing between them.
- Stage 16 plugs that third specialist into the existing supervisor+critic
  graph from Stage 13/14. Nothing about the supervisor's routing mechanism
  or the critic's review logic changes shape - the routing field's type
  just widens from two literal values to three, and the two conditional
  edges (supervisor -> specialist, critic -> specialist-or-end) each gain
  one more entry in their dispatch dict. The critic itself needs zero code
  changes: it only ever reads the original question and the latest answer,
  never which specialist produced it.

Architecture:

    START -> supervisor -> (conditional edge on state["next"])
                                |-> research_agent   -\
                                |-> knowledge_agent  --+-> critic
                                |-> analysis_agent   -/
                                                          |
                                            (conditional edge on state["verdict"])
                                                |-> "pass"  -> END
                                                |-> "retry" -> back to the
                                                               SAME specialist
                                                               (state["next"])

All three specialists are copied from their own stages with no changes to
their own agent -> tools -> agent loop. The only new pieces relative to
Stage 14 are the Analysis Agent's subgraph, its wrapper node, its entry in
the supervisor's system prompt, and its entry in both conditional-edge
dispatch dicts.
"""

import ast
import operator
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
# Research Agent - web search specialist (copied from Stage 14, unchanged)
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
# Knowledge Agent - local knowledge-base specialist (copied from Stage 14,
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
# Analysis Agent - safe-arithmetic specialist (ported from Stage 15; the
# `calculate` tool and its AST-walker are unchanged, the node function is
# renamed agent -> analysis_agent_node to match the other two specialists'
# naming, and the subgraph is compiled without its own checkpointer since
# the outer supervisor graph owns state here).
# ---------------------------------------------------------------------------

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _ALLOWED_BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


@tool
def calculate(expression: str) -> str:
    """Evaluate a numeric arithmetic expression and return the result.

    Supports +, -, *, /, // (floor division), % (modulo), ** (power), and
    parentheses. Use this for anything requiring exact arithmetic - sums,
    averages (e.g. "(12 + 18 + 30) / 3"), percentage change
    (e.g. "((120 - 100) / 100) * 100"), or differences between values -
    instead of computing it yourself, since arithmetic done by an LLM is
    unreliable.
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree)
    except Exception as exc:
        return f"Could not evaluate '{expression}': {exc}"
    return str(result)


analysis_tools = [calculate]

ANALYSIS_SYSTEM_PROMPT = (
    "You are an Analysis Agent, a specialist in calculations, comparisons, "
    "and reasoning over numeric or structured data. You work only with "
    "numbers and data given to you in the conversation - you cannot search "
    "the web or read documents. You have one tool: calculate, which "
    "evaluates arithmetic expressions exactly. Use it for any sum, average, "
    "percentage change, or difference instead of computing by hand, since "
    "you are prone to arithmetic mistakes. For comparisons like 'which is "
    "highest', reason directly over the numbers once you have them. Stay "
    "focused on analysis - you're not a general-purpose assistant."
)

analysis_llm = ChatOpenAI(model="gpt-4o-mini")
analysis_llm_with_tools = analysis_llm.bind_tools(analysis_tools)


def analysis_agent_node(state: MessagesState):
    messages = [SystemMessage(content=ANALYSIS_SYSTEM_PROMPT), *state["messages"]]
    response = analysis_llm_with_tools.invoke(messages)
    return {"messages": [response]}


analysis_subgraph_builder = StateGraph(MessagesState)
analysis_subgraph_builder.add_node("agent", analysis_agent_node)
analysis_subgraph_builder.add_node("tools", ToolNode(analysis_tools))
analysis_subgraph_builder.add_edge(START, "agent")
analysis_subgraph_builder.add_conditional_edges("agent", tools_condition)
analysis_subgraph_builder.add_edge("tools", "agent")

analysis_graph = analysis_subgraph_builder.compile()


# ---------------------------------------------------------------------------
# Supervisor - routing decision (copied from Stage 14, widened from two
# specialists to three: SupervisorState/Route's "next" literal gains
# "analysis", and the system prompt gains a third bullet).
# ---------------------------------------------------------------------------


class CriticState(MessagesState):
    next: Literal["research", "knowledge", "analysis"]
    verdict: Literal["pass", "retry"]
    feedback: str
    retry_count: int


class Route(TypedDict):
    """The supervisor's routing decision."""

    next: Literal["research", "knowledge", "analysis"]


SUPERVISOR_SYSTEM_PROMPT = (
    "You are a supervisor that routes a user's question to exactly one of "
    "three specialist agents:\n\n"
    "- 'research': a Research Agent that searches the live web. Use this "
    "for current events, recent news, or anything that changes over time "
    "and needs up-to-date information.\n"
    "- 'knowledge': a Knowledge Agent that searches a local knowledge base "
    "covering renewable energy topics (solar, wind, and hydro power). Use "
    "this for questions about those topics.\n"
    "- 'analysis': an Analysis Agent that performs exact arithmetic - sums, "
    "averages, percentage changes, and comparisons - over numbers already "
    "given in the conversation. Use this for calculation questions; it "
    "cannot search the web or read documents.\n\n"
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


def analysis_node(state: CriticState):
    """Run the Analysis Agent subgraph and hand back only its final answer.

    Same feedback-on-retry addition as research_node/knowledge_node above.
    """
    messages = state["messages"]
    if state.get("feedback"):
        messages = messages + [
            HumanMessage(
                content=f"Reviewer feedback: {state['feedback']} "
                "Please address this and try again."
            )
        ]
    result = analysis_graph.invoke({"messages": messages})
    return {"messages": [result["messages"][-1]]}


def route_from_supervisor(state: CriticState) -> str:
    return state["next"]


# ---------------------------------------------------------------------------
# Critic - unchanged from Stage 14. It only ever reads the original
# question (state["messages"][0]) and the latest answer
# (state["messages"][-1]), so it needed no changes to support a third
# specialist - only the conditional edge that routes a retry back to the
# same specialist gained a third dispatch entry.
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
supervisor_graph_builder.add_node("analysis_agent", analysis_node)
supervisor_graph_builder.add_node("critic", critic_node)

supervisor_graph_builder.add_edge(START, "supervisor")
supervisor_graph_builder.add_conditional_edges(
    "supervisor",
    route_from_supervisor,
    {
        "research": "research_agent",
        "knowledge": "knowledge_agent",
        "analysis": "analysis_agent",
    },
)
supervisor_graph_builder.add_edge("research_agent", "critic")
supervisor_graph_builder.add_edge("knowledge_agent", "critic")
supervisor_graph_builder.add_edge("analysis_agent", "critic")
supervisor_graph_builder.add_conditional_edges(
    "critic",
    route_from_critic,
    {
        "research": "research_agent",
        "knowledge": "knowledge_agent",
        "analysis": "analysis_agent",
        "end": END,
    },
)

graph = supervisor_graph_builder.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# REPL - one graph, no prefixes. The supervisor routes to one of three
# specialists, the critic reviews.
# ---------------------------------------------------------------------------

def main():
    config = {"configurable": {"thread_id": "supervisor-1"}}

    print("Stage 16: supervisor routes your question to one of three specialists.")
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
        print(f"[Critic verdict: {result['verdict']}, retries used: {result['retry_count']}]")
        print(f"[{result['next'].capitalize()} Agent]: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
