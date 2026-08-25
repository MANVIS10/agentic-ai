"""
Stage 19: wrap Stage 18's exact multi-agent graph in a FastAPI HTTP API
instead of a terminal REPL, so the same Postgres-backed conversation (the
plan, subtasks, results collected so far, and a paused-at-`human_approval`
interrupt) can be driven by any HTTP client, not just a person typing into
`input()`.

New concept vs. Stage 1-18:
- Every earlier stage's entrypoint was a blocking REPL: `run_until_settled`
  called `graph.invoke(...)`, and if the graph paused on `interrupt()`, the
  same process blocked on `input()` until a human typed y/n, then resumed
  with `Command(resume=...)` in that same call stack. There is no `input()`
  over HTTP - a request has to return a response and end.
- So `interrupt()`/`Command(resume=...)` now span two separate HTTP
  requests instead of one blocking loop: `POST /chat` runs the graph until
  it pauses at `human_approval` and returns the plan + approval prompt
  immediately; the human's decision arrives later, as its own
  `POST /approve` or `POST /reject` request, which resumes the SAME
  `thread_id` with `Command(resume="y"/"n")`. The graph itself doesn't know
  or care that its caller changed - `interrupt()` pauses execution inside a
  node and hands control back to whatever called `.invoke()`, REPL loop or
  HTTP route handler alike.
- Because a client can now call `/approve` on a thread_id at any time -
  including one that was never started, or one that already finished -
  `graph.get_state(config)` stops being a nice-to-have debugging tool (as
  it was in Stage 18's `verify_persistence.py`) and becomes load-bearing:
  it's how `/approve` and `/reject` check "is this thread actually paused
  waiting for me?" before calling `Command(resume=...)`, since the old
  signal ("did invoke() just hand me back an interrupt?") isn't available
  across two separate requests.
- Nothing about the graph itself changed. Every node, edge, tool, and
  prompt below is copied verbatim from Stage 18 - proving (the same way
  Stage 18 proved a checkpointer is a pluggable backend) that a compiled
  `StateGraph` is just as callable from inside a FastAPI route handler as
  from a REPL loop or a test script.

See this folder's README.md for how to start Postgres, run the API, and
exercise all four endpoints.
"""

import ast
import operator
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import psycopg
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt
from pydantic import BaseModel
from typing_extensions import TypedDict

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/postgres?sslmode=disable"
)

MAX_RETRIES = 1  # at most one retry per subtask - two specialist attempts total

llm = ChatOpenAI(model="gpt-4o-mini")


# ---------------------------------------------------------------------------
# Research Agent - web search specialist (copied from Stage 18, unchanged)
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
# Knowledge Agent - local knowledge-base specialist (copied from Stage 18,
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
# Analysis Agent - safe-arithmetic specialist (copied from Stage 18,
# unchanged)
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
# Supervisor + Critic - routing and review (copied from Stage 18, unchanged)
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
# "json_schema": gpt-4o-mini's default structured-output mode occasionally
# echoes back the JSON SCHEMA itself instead of an instance of it, which
# crashes a dict lookup with a KeyError. function_calling reliably returns
# just the typed fields.
supervisor_llm = ChatOpenAI(model="gpt-4o-mini").with_structured_output(
    Route, method="function_calling"
)


def supervisor_node(state: CriticState):
    messages = [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT), *state["messages"]]
    route: Route = supervisor_llm.invoke(messages)
    return {"next": route["next"], "retry_count": 0}


def research_node(state: CriticState):
    """Run the Research Agent subgraph and hand back only its final answer."""
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
    """Run the Knowledge Agent subgraph and hand back only its final answer."""
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
    """Run the Analysis Agent subgraph and hand back only its final answer."""
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


supervisor_critic_builder = StateGraph(CriticState)
supervisor_critic_builder.add_node("supervisor", supervisor_node)
supervisor_critic_builder.add_node("research_agent", research_node)
supervisor_critic_builder.add_node("knowledge_agent", knowledge_node)
supervisor_critic_builder.add_node("analysis_agent", analysis_node)
supervisor_critic_builder.add_node("critic", critic_node)

supervisor_critic_builder.add_edge(START, "supervisor")
supervisor_critic_builder.add_conditional_edges(
    "supervisor",
    route_from_supervisor,
    {
        "research": "research_agent",
        "knowledge": "knowledge_agent",
        "analysis": "analysis_agent",
    },
)
supervisor_critic_builder.add_edge("research_agent", "critic")
supervisor_critic_builder.add_edge("knowledge_agent", "critic")
supervisor_critic_builder.add_edge("analysis_agent", "critic")
supervisor_critic_builder.add_conditional_edges(
    "critic",
    route_from_critic,
    {
        "research": "research_agent",
        "knowledge": "knowledge_agent",
        "analysis": "analysis_agent",
        "end": END,
    },
)

# No checkpointer here on purpose: this graph is invoked as a one-shot
# helper once per subtask (like Stage 8's research_agent), not run as its
# own multi-turn REPL the way Stage 16 runs it. Only the OUTER planner graph
# below needs a checkpointer, for its one interrupt().
supervisor_critic_graph = supervisor_critic_builder.compile()


# ---------------------------------------------------------------------------
# Planner + human approval (copied from Stage 18, unchanged)
# ---------------------------------------------------------------------------


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

    # Reset every field a later node might set, not just the ones this node
    # itself uses. plan() is the one node guaranteed to run first on every
    # turn (START -> plan), so it's the only place that can undo a stale
    # final_answer/approved left behind by an earlier question on the same
    # thread.
    return {
        "subtasks": subtasks,
        "current_index": 0,
        "results": [],
        "final_answer": "",
        "approved": False,
    }


def human_approval(state: PlannerState):
    decision = interrupt("Approve this plan? (y/n): ")
    return {"approved": decision.strip().lower() == "y"}


def route_after_approval(state: PlannerState) -> str:
    if not state["approved"]:
        return END
    # Don't assume plan() produced at least one subtask - reuse the same
    # "anything left to research?" check used between subtask loops, so an
    # empty plan goes straight to synthesize instead of indexing into an
    # empty subtasks list in research_subtask.
    return has_more_subtasks(state)


def research_subtask(state: PlannerState):
    """Research one subtask by running it through the full
    supervisor -> specialist -> critic pipeline, instead of a bare LLM call
    (Stage 6/7) or a single flat tool agent (Stage 8).

    Each subtask gets a fresh invocation - no shared thread/state carries
    over between subtasks, so retry_count always starts at 0 for each one.
    """
    subtask = state["subtasks"][state["current_index"]]
    print(f"\nResearching: {subtask}")

    result = supervisor_critic_graph.invoke(
        {"messages": [{"role": "user", "content": subtask}]}
    )
    print(f"  [Supervisor routed to: {result['next']}]")
    print(f"  [Critic verdict: {result['verdict']}, retries used: {result['retry_count']}]")

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

# Same checkpointer setup as Stage 18: a PostgresSaver backed by a real
# database, so this graph's checkpoints (the plan, subtasks, results-so-far,
# and any paused-at-human_approval interrupt) outlive the Python process -
# now doubly important, since a paused thread here is waiting on a SEPARATE
# HTTP request (/approve or /reject), not the next line of the same REPL
# loop. The connection is opened once at module scope so `graph` (and
# `checkpointer`) exist as importable, already-connected module-level names
# wherever this module is imported from - by uvicorn when it loads `app`,
# or by this stage's own test/verification scripts (`from main import
# app, checkpointer`).
pg_conn = psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=0)
checkpointer = PostgresSaver(pg_conn)
checkpointer.setup()  # idempotent: creates the checkpoint tables on first run only
graph = graph_builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Per-thread_id locking - an in-process guard in front of /chat, /approve,
# and /reject, closing a race that has nothing to do with the graph or
# Postgres being wrong: graph.invoke({"question": ...}, config) in /chat
# takes real wall-clock time (plan()'s LLM call), and doesn't commit its
# "paused at human_approval" checkpoint until it returns. If /approve or
# /reject for the SAME thread_id runs graph.get_state(config) before that
# commit lands - e.g. a client that doesn't wait for /chat's response
# before firing the next request - it reads whatever checkpoint was
# PREVIOUSLY the latest for that thread_id (nothing, for a brand-new
# thread_id -> 404; a prior completed run, for a reused one -> a stale,
# misleading 409 "not currently awaiting approval").
#
# The fix isn't in the checkpointer or the graph - both are already correct
# and consistent, just read at the wrong moment. It's mutual exclusion
# around "read/act on this thread_id's state", scoped to one Python process
# (this stage runs as a single sync process - see the README's "Design
# decisions" for why that's the right scope here, and what would need to
# change for a multi-worker deployment).
# ---------------------------------------------------------------------------

# How long a request will wait for another request already in flight on the
# same thread_id before giving up. Generous on purpose: /approve can hold
# this for the entire research_subtask loop (multiple specialist + critic
# calls across 2-3 subtasks, with possible retries) - not just the few
# seconds /chat's plan() call takes.
THREAD_LOCK_TIMEOUT_SECONDS = 120

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()  # protects _thread_locks itself, not held while a per-thread lock is held


def _lock_for(thread_id: str) -> threading.Lock:
    """One Lock per thread_id, created on first use. Different thread_ids
    never block each other - only two requests for the SAME thread_id
    contend.
    """
    with _thread_locks_guard:
        if thread_id not in _thread_locks:
            _thread_locks[thread_id] = threading.Lock()
        return _thread_locks[thread_id]


@contextmanager
def _thread_lock(thread_id: str):
    """Hold this thread_id's lock for the life of one request, so a
    concurrent /chat, /approve, or /reject for the SAME thread_id can never
    read Postgres state while this request is still in the middle of
    changing it. Raises the same 409 /approve and /reject already use for
    "not currently awaiting approval" if the other request hasn't finished
    within THREAD_LOCK_TIMEOUT_SECONDS, rather than blocking forever.
    """
    lock = _lock_for(thread_id)
    if not lock.acquire(timeout=THREAD_LOCK_TIMEOUT_SECONDS):
        raise HTTPException(
            status_code=409,
            detail="This thread is busy processing another request. Please try again shortly.",
        )
    try:
        yield
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# FastAPI layer - everything below this point is new for Stage 19.
#
# Stage 18's run_until_settled()/main() REPL loop is gone: there's no
# input() over HTTP. Instead:
#   - POST /chat runs the graph until it pauses at human_approval and
#     returns the plan + approval prompt right away.
#   - POST /approve / POST /reject resume that SAME thread_id later, as
#     their own separate requests, via Command(resume="y"/"n").
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    question: str
    thread_id: str


class ApproveRequest(BaseModel):
    thread_id: str


class RejectRequest(BaseModel):
    thread_id: str


class ThreadStatusResponse(BaseModel):
    """Shared response shape for /chat, /approve, and /reject - which
    optional fields are populated depends on `status`:
      - "awaiting_approval" (only ever returned by /chat, given this
        graph's fixed shape): subtasks + approval_prompt are set.
      - "completed" (returned by /approve): subtasks, results, and
        final_answer are all set.
      - "rejected" (returned by /reject): subtasks is set (the plan that
        was declined); results is []; final_answer is "".
    """

    thread_id: str
    status: Literal["awaiting_approval", "completed", "rejected"]
    subtasks: list[str] | None = None
    approval_prompt: str | None = None
    results: list[str] | None = None
    final_answer: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["connected"]


app = FastAPI(
    title="Stage 19: Multi-Agent Research Assistant API",
    description=(
        "FastAPI wrapper around Stage 18's planner + human-approval + "
        "supervisor/critic/specialist research graph, checkpointed to "
        "PostgreSQL."
    ),
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Defense-in-depth net for anything that escapes a route's own
    try/except below (e.g. a failure while parsing the request itself).
    Never echoes exc's text back to the client - only logs it server-side.
    """
    print(f"[unhandled] {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "An unexpected error occurred."})


@app.get("/health", response_model=HealthResponse)
def health():
    """Verify the API process is up AND its Postgres dependency is
    reachable - a plain "the process is running" check would pass even if
    the database (the thing every other endpoint actually depends on) were
    down.
    """
    try:
        pg_conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        print(f"[/health] Database unavailable: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")
    return HealthResponse(status="ok", database="connected")


@app.post("/chat", response_model=ThreadStatusResponse)
def chat(request: ChatRequest):
    """Start (or restart) a research question on the given thread_id.

    Because human_approval() unconditionally calls interrupt(), this always
    pauses there and returns - it never runs research_subtask/synthesize in
    the same call. Approval/rejection happens via separate requests below.

    Held under this thread_id's lock for the whole call: plan()'s LLM call
    takes real wall-clock time, and a concurrent /approve or /reject for the
    same thread_id must not be able to read Postgres until the checkpoint
    this call is about to write has actually committed.
    """
    with _thread_lock(request.thread_id):
        config = {"configurable": {"thread_id": request.thread_id}}

        try:
            result = graph.invoke({"question": request.question}, config=config)
        except Exception as exc:
            print(f"[/chat] Error for thread_id={request.thread_id!r}: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong processing this question. Please try again.",
            )

        if "__interrupt__" in result:
            prompt = result["__interrupt__"][0].value
            return ThreadStatusResponse(
                thread_id=request.thread_id,
                status="awaiting_approval",
                subtasks=result.get("subtasks", []),
                approval_prompt=prompt,
            )

        # Shouldn't happen given the current graph (human_approval always
        # interrupts) - kept as a safety net rather than assuming the shape
        # above is the only possible outcome.
        return ThreadStatusResponse(
            thread_id=request.thread_id,
            status="completed",
            subtasks=result.get("subtasks", []),
            results=result.get("results", []),
            final_answer=result.get("final_answer", ""),
        )


def _require_pending_approval(thread_id: str):
    """Shared validation for /approve and /reject: confirm this thread_id
    actually exists and is currently paused at human_approval before
    calling Command(resume=...) on it. graph.invoke()'s return value alone
    can't answer this anymore, since the approval decision now arrives as
    its own separate request instead of the same call that produced the
    interrupt.
    """
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)

    if not state.values:
        raise HTTPException(
            status_code=404, detail="No conversation found for this thread_id"
        )
    if "human_approval" not in state.next:
        raise HTTPException(
            status_code=409, detail="This thread is not currently awaiting approval"
        )
    return config


@app.post("/approve", response_model=ThreadStatusResponse)
def approve(request: ApproveRequest):
    """Resume a paused thread with an approval, running
    research_subtask (looped over every subtask) -> synthesize -> END.

    Held under this thread_id's lock for the whole call, so the pending-
    approval check and the resume happen atomically together: a concurrent
    /chat for the same thread_id can't slip a new checkpoint in between the
    check and the resume, and a duplicate simultaneous /approve is forced to
    wait and then see this call's result rather than racing it.
    """
    with _thread_lock(request.thread_id):
        config = _require_pending_approval(request.thread_id)

        try:
            result = graph.invoke(Command(resume="y"), config=config)
        except Exception as exc:
            print(f"[/approve] Error for thread_id={request.thread_id!r}: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong while processing the approved plan. Please try again.",
            )

        return ThreadStatusResponse(
            thread_id=request.thread_id,
            status="completed",
            subtasks=result.get("subtasks", []),
            results=result.get("results", []),
            final_answer=result.get("final_answer", ""),
        )


@app.post("/reject", response_model=ThreadStatusResponse)
def reject(request: RejectRequest):
    """Resume a paused thread with a rejection. route_after_approval sends
    this straight to END - no special-case handling needed here, results
    stays [] and final_answer stays "" since no research ever ran.

    Held under this thread_id's lock for the same reason /approve is - see
    its docstring.
    """
    with _thread_lock(request.thread_id):
        config = _require_pending_approval(request.thread_id)

        try:
            result = graph.invoke(Command(resume="n"), config=config)
        except Exception as exc:
            print(f"[/reject] Error for thread_id={request.thread_id!r}: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong while processing the rejection. Please try again.",
            )

        return ThreadStatusResponse(
            thread_id=request.thread_id,
            status="rejected",
            subtasks=result.get("subtasks", []),
            results=result.get("results", []),
            final_answer=result.get("final_answer", ""),
        )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
