"""
Stage 22: wire the Knowledge Agent up to Stage 21's pgvector search, so a
research question can actually be answered from documents a user uploaded
- Stage 21 made those chunks searchable but never let an LLM read a
result.

New concept vs. Stage 1-21:
- A specialist's tool can be swapped out entirely without touching the
  supervisor, critic, or planner layers above it. knowledge_node (in the
  supervisor+critic graph below) only ever calls
  knowledge_graph.invoke(...) and never references a tool by name, so
  replacing what's bound inside knowledge_graph's own subgraph required
  zero changes anywhere else - the same "critic needs no changes" lesson
  Stage 16 proved for adding a specialist, one layer deeper: it also holds
  for changing what's INSIDE an existing one.
- Explicit design decision (confirmed with the user, see
  .claude/spec/stage22_knowledge_agent_rag_spec.md): this is a
  REPLACEMENT, not an addition. The Knowledge Agent's tool is
  search_uploaded_documents (pgvector over document_chunks) INSTEAD OF
  search_knowledge_base (the bundled knowledge_base/*.md via
  InMemoryVectorStore) - not both side by side. For normal queries in this
  stage, the bundled knowledge base is unreachable.
- search_knowledge_base and knowledge_base/*.md are NOT touched or carried
  forward here. They remain exactly as they were in Stage 3, 8, 10, 16-21
  - kept for historical compatibility, per explicit instruction, not
  reused or referenced by this stage's code. knowledge_base/ is not even
  duplicated into this folder, since nothing here reads it.
- Everything else - Research Agent, Analysis Agent, the supervisor+critic
  graph, the outer planner+human-approval graph, PostgresSaver
  checkpointing, per-thread_id locking, and every existing route
  (/health, /chat, /approve, /reject, /documents/upload,
  /documents/backfill-embeddings, /documents/search) - is copied verbatim
  from Stage 21. Nothing about the graph's shape or routing changed.

Requires the same docker-compose.yml pgvector setup as Stage 21
(image: pgvector/pgvector:pg16) - see this folder's README.md.
"""

import ast
import io
import operator
import os
import threading
import uuid
from contextlib import contextmanager
from typing import Literal

import psycopg
import uvicorn
from docx import Document as DocxDocument
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt
from pgvector import Vector
from pgvector.psycopg import register_vector
from pydantic import BaseModel
from pypdf import PdfReader
from typing_extensions import TypedDict

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/postgres?sslmode=disable"
)

MAX_RETRIES = 1  # at most one retry per subtask - two specialist attempts total

llm = ChatOpenAI(model="gpt-4o-mini")


# ---------------------------------------------------------------------------
# Research Agent - web search specialist (copied from Stage 21, unchanged)
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
# Knowledge Agent - now searches user-uploaded documents (Stage 20/21's
# document_chunks table, via pgvector) instead of the bundled
# knowledge_base/*.md files. search_knowledge_base and knowledge_base/*.md
# are NOT carried forward into this stage - they remain untouched in
# Stage 3-21 as a historical reference for the earlier, simpler retrieval
# pattern. See .claude/spec/stage22_knowledge_agent_rag_spec.md sections
# 3 and 6 for the full reasoning behind this replacement.
# ---------------------------------------------------------------------------

KNOWLEDGE_SYSTEM_PROMPT = (
    "You are a Knowledge Agent, a specialist whose only job is answering "
    "questions from documents the user has uploaded. You have one tool: "
    "uploaded-document search. You cannot browse the web, access the "
    "project's built-in reference material, or anything outside what the "
    "user has uploaded. If no uploaded document contains the answer - "
    "including if no documents have been uploaded at all - say so plainly "
    "instead of guessing. Stay focused on uploaded documents - you're not "
    "a general-purpose assistant."
)

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# Matches search_knowledge_base's existing k=3 - a fixed internal agent
# tool, not a testing endpoint a caller tunes per request (unlike
# /documents/search's configurable top_k).
KNOWLEDGE_TOOL_K = 3


@tool
def search_uploaded_documents(query: str) -> str:
    """Search documents the user has uploaded for information relevant to
    a natural-language question and return the most relevant text chunks.

    Use this for any question that might be answered by a document the
    user has uploaded. There is no other knowledge source available.
    """
    try:
        query_embedding = embeddings.embed_query(query)
    except Exception as exc:
        print(f"[search_uploaded_documents] Embedding error: {exc}")
        return "Something went wrong while searching uploaded documents."

    try:
        rows = pg_conn.execute(
            """
            SELECT dc.content, d.filename
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.embedding IS NOT NULL
            ORDER BY dc.embedding <=> %s
            LIMIT %s
            """,
            (Vector(query_embedding), KNOWLEDGE_TOOL_K),
        ).fetchall()
    except Exception as exc:
        print(f"[search_uploaded_documents] DB error: {exc}")
        return "Something went wrong while searching uploaded documents."

    # No similarity threshold is applied, matching search_knowledge_base's
    # own threshold-free k=3 search. Without one, ORDER BY ... LIMIT always
    # returns the closest k chunks whenever ANY embedded chunk exists, so
    # the only reachable empty case is "no uploaded chunks have an
    # embedding at all" - not "documents exist but none are relevant".
    # When documents exist but are a poor topical match, the specialist LLM
    # judges that itself from the returned content, same as
    # search_knowledge_base has always relied on it to do.
    if not rows:
        return "No documents have been uploaded yet."

    formatted = [f"[source: {filename}]\n{content}" for content, filename in rows]
    return "\n\n".join(formatted)


knowledge_tools = [search_uploaded_documents]

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
# Analysis Agent - safe-arithmetic specialist (copied from Stage 21,
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
# Supervisor + Critic - routing and review (copied from Stage 21, unchanged)
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
    "- 'knowledge': a Knowledge Agent that searches documents the user has "
    "uploaded. Use this for questions that could be answered by a document "
    "the user has provided.\n"
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
# Planner + human approval (copied from Stage 21, unchanged)
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

# Same checkpointer setup as Stage 21: a PostgresSaver backed by a real
# database, so this graph's checkpoints (the plan, subtasks, results-so-far,
# and any paused-at-human_approval interrupt) outlive the Python process.
# The connection is opened once at module scope so `graph` (and
# `checkpointer`) exist as importable, already-connected module-level names
# wherever this module is imported from - by uvicorn when it loads `app`,
# or by this stage's own test script (`from main import app, pg_conn`).
pg_conn = psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=0)
checkpointer = PostgresSaver(pg_conn)
checkpointer.setup()  # idempotent: creates the checkpoint tables on first run only
graph = graph_builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Document upload tables (Stage 20, unchanged). Created idempotently at
# module load, the same "safe to call every process start" convention as
# checkpointer.setup() just above - but hand-written SQL, since these two
# tables aren't owned by any library the way the checkpoint tables are
# owned by langgraph-checkpoint-postgres. No migrations framework, per this
# project's minimal-dependencies rule.
# ---------------------------------------------------------------------------

DOCUMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

DOCUMENT_CHUNKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

pg_conn.execute(DOCUMENTS_TABLE_SQL)
pg_conn.execute(DOCUMENT_CHUNKS_TABLE_SQL)  # after documents - it has a FK reference to it


# ---------------------------------------------------------------------------
# pgvector setup (Stage 21, unchanged). Idempotent, same "safe to run every
# process start" convention as checkpointer.setup() and the DDL above. The
# extension must exist before `vector` is a usable column type and before
# register_vector() can look up its type OID.
# ---------------------------------------------------------------------------

pg_conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
pg_conn.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(1536)")
register_vector(pg_conn)


# ---------------------------------------------------------------------------
# Document upload pipeline (Stage 20/21, unchanged): validate -> extract ->
# chunk -> embed -> store.
# ---------------------------------------------------------------------------

ALLOWED_FILE_TYPES = {"pdf", "txt", "docx"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

CHUNK_SIZE = 400
CHUNK_OVERLAP = 50
document_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
)


def get_file_type(filename: str) -> str | None:
    """Return 'pdf'/'txt'/'docx' from the filename's extension, or None if
    unsupported (or the filename has no extension at all). Extension-based,
    not UploadFile.content_type - multipart clients set that inconsistently
    (many send application/octet-stream for anything), so it isn't a
    reliable signal on its own.
    """
    if "." not in filename:
        return None
    extension = filename.rsplit(".", 1)[-1].lower()
    return extension if extension in ALLOWED_FILE_TYPES else None


def extract_text(file_bytes: bytes, file_type: str) -> str:
    """Extract raw text from the uploaded bytes for a supported file_type.

    Raises on a corrupt/unparseable file - the caller (upload_document)
    catches that and maps it to a 422. This doubles as the real, content-
    based check that a file's extension didn't lie: a .pdf-named file that
    isn't actually a valid PDF fails here, inside PdfReader, rather than
    being silently accepted.
    """
    if file_type == "pdf":
        reader = PdfReader(io.BytesIO(file_bytes))
        return " ".join(
            " ".join((page.extract_text() or "").split()) for page in reader.pages
        )
    if file_type == "docx":
        document = DocxDocument(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    # txt - strict decode (no errors="ignore") gives this the same
    # "content is the real check" property as PDF/DOCX above: a non-UTF-8
    # file raises instead of silently losing bytes.
    return file_bytes.decode("utf-8")


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
# around "read/act on this thread_id's state", scoped to one Python process.
# Copied from Stage 21, unchanged - the document routes below don't use it,
# since documents aren't scoped to a thread_id.
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
# FastAPI layer. Every route is copied verbatim from Stage 21 - this stage
# only changes what's bound inside the Knowledge Agent subgraph above.
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


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    status: Literal["stored"]


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    similarity_threshold: float | None = None
    # Kept as plain str, NOT uuid.UUID: if Pydantic parsed this field, a
    # malformed value would auto-422 before this route body ever runs, but
    # the spec only defines a 404 for "does not exist" - parsing by hand
    # below (with a try/except ValueError mapped to that same 404) is what
    # makes "malformed and unknown both -> 404" actually true.
    document_id: str | None = None


class SearchResult(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    content: str
    similarity: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


class BackfillResponse(BaseModel):
    chunks_found: int
    embedded_count: int
    failed_count: int


app = FastAPI(
    title="Stage 22: Multi-Agent Research Assistant API with Knowledge Agent RAG",
    description=(
        "FastAPI wrapper around Stage 21's planner + human-approval + "
        "supervisor/critic/specialist research graph plus document upload "
        "and semantic search, checkpointed to PostgreSQL. The Knowledge "
        "Agent now answers from user-uploaded documents (via pgvector) "
        "instead of the project's bundled knowledge base."
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


UNSUPPORTED_TYPE_DETAIL = "Unsupported file type. Allowed types: pdf, txt, docx"


@app.post("/documents/upload", response_model=UploadResponse)
def upload_document(file: UploadFile = File(...)):
    """Validate, extract, chunk, embed, and durably store an uploaded
    PDF/TXT/DOCX file. Every chunk this route writes already has its
    embedding attached - no backfill ever needed for uploads that go
    through this route.

    Validation order: extension -> read bytes -> empty -> size limit ->
    extract (content-based check #2) -> empty-extracted-text -> chunk ->
    embed -> store. Error-handling style matches every other route: a
    short, hand-written detail string on every HTTPException, the real
    exception printed server-side, never echoed to the client.
    """
    filename = file.filename or ""
    file_type = get_file_type(filename)
    if file_type is None:
        raise HTTPException(status_code=400, detail=UNSUPPORTED_TYPE_DETAIL)

    # Sync read, same as every other route here being a plain `def` -
    # FastAPI/Starlette run sync routes in a threadpool automatically, so
    # this blocking call doesn't need to be async.
    file_bytes = file.file.read()

    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    try:
        text = extract_text(file_bytes, file_type)
    except Exception as exc:
        print(f"[/documents/upload] Extraction failed for {filename!r}: {exc}")
        raise HTTPException(
            status_code=422,
            detail="Could not read this file — it may be corrupted or malformed",
        )

    if not text.strip():
        raise HTTPException(
            status_code=422, detail="No extractable text found in this document"
        )

    chunks = document_splitter.split_text(text)
    document_id = uuid.uuid4()

    # One batched embedding call for the whole document. This runs entirely
    # BEFORE the transaction below opens, so a failure here leaves nothing
    # written - the whole upload fails rather than storing some chunks with
    # an embedding and others without.
    try:
        chunk_embeddings = embeddings.embed_documents(chunks)
    except Exception as exc:
        print(f"[/documents/upload] Embedding failed for {filename!r}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while storing this document. Please try again.",
        )

    try:
        # pg_conn is autocommit; .transaction() still gives an explicit
        # BEGIN/COMMIT/ROLLBACK block on it, so a failure partway through
        # chunk insertion rolls back the whole thing - no orphaned
        # documents row with a wrong chunk_count or a partial chunk set.
        with pg_conn.transaction():
            pg_conn.execute(
                "INSERT INTO documents (id, filename, file_type, file_size_bytes, chunk_count) "
                "VALUES (%s, %s, %s, %s, %s)",
                (document_id, filename, file_type, len(file_bytes), len(chunks)),
            )
            for index, chunk_text in enumerate(chunks):
                pg_conn.execute(
                    "INSERT INTO document_chunks (id, document_id, chunk_index, content, embedding) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        uuid.uuid4(),
                        document_id,
                        index,
                        chunk_text,
                        Vector(chunk_embeddings[index]),
                    ),
                )
    except Exception as exc:
        print(f"[/documents/upload] DB write failed for {filename!r}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while storing this document. Please try again.",
        )

    return UploadResponse(
        document_id=str(document_id),
        filename=filename,
        file_type=file_type,
        chunk_count=len(chunks),
        status="stored",
    )


@app.post("/documents/backfill-embeddings", response_model=BackfillResponse)
def backfill_embeddings():
    """Embed every document_chunks row with embedding IS NULL - rows
    written before pgvector existed, or leftovers from a previous partial
    backfill run.

    Each chunk is embedded and UPDATEd independently (one call per chunk,
    not one giant batch), each in its own try/except: one bad chunk never
    blocks the rest, every successful UPDATE commits immediately on this
    autocommit connection, and a retry of this endpoint only ever touches
    rows still NULL - it's naturally resumable without any extra state.
    """
    rows = pg_conn.execute(
        "SELECT id, content FROM document_chunks WHERE embedding IS NULL"
    ).fetchall()

    embedded_count = 0
    failed_count = 0
    for chunk_id, content in rows:
        try:
            chunk_embedding = embeddings.embed_documents([content])[0]
            pg_conn.execute(
                "UPDATE document_chunks SET embedding = %s WHERE id = %s",
                (Vector(chunk_embedding), chunk_id),
            )
            embedded_count += 1
        except Exception as exc:
            print(f"[/documents/backfill-embeddings] Failed to embed chunk {chunk_id}: {exc}")
            failed_count += 1

    return BackfillResponse(
        chunks_found=len(rows), embedded_count=embedded_count, failed_count=failed_count
    )


@app.post("/documents/search", response_model=SearchResponse)
def search_documents(request: SearchRequest):
    """Semantic similarity search over embedded document_chunks. Kept for
    direct testing/inspection of the search layer, independent of the
    Knowledge Agent's own tool (search_uploaded_documents above), which
    calls the same underlying query in-process rather than this route. An
    empty results list is a valid 200, not an error, whenever nothing
    clears similarity_threshold or no chunk in scope has an embedding yet.
    """
    query_text = request.query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Query text cannot be empty")
    if request.top_k < 1:
        raise HTTPException(status_code=400, detail="top_k must be a positive integer")
    if request.similarity_threshold is not None and not (
        0 <= request.similarity_threshold <= 1
    ):
        raise HTTPException(
            status_code=400, detail="similarity_threshold must be between 0 and 1"
        )

    document_uuid = None
    if request.document_id is not None:
        try:
            document_uuid = uuid.UUID(request.document_id)
        except ValueError:
            raise HTTPException(
                status_code=404, detail="No document found for this document_id"
            )
        exists = pg_conn.execute(
            "SELECT 1 FROM documents WHERE id = %s", (document_uuid,)
        ).fetchone()
        if exists is None:
            raise HTTPException(
                status_code=404, detail="No document found for this document_id"
            )

    try:
        query_embedding = embeddings.embed_query(query_text)
    except Exception as exc:
        print(f"[/documents/search] Embedding query failed: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while processing this search. Please try again.",
        )

    # A subquery, not a flat SELECT: Postgres won't let a WHERE clause
    # reference a SELECT-list alias in the same query, but an OUTER query
    # can reference an inner query's output column by name - so `similarity`
    # is computed once in the inner SELECT and reused by both the outer
    # WHERE (similarity_threshold) and ORDER BY, instead of recomputing the
    # <=> operator a second time. Only ever appends pre-written static
    # clause text based on which optional filters are present - every
    # actual value still goes through the params list below, never
    # string-interpolated.
    sql = """
        SELECT * FROM (
            SELECT dc.id AS chunk_id, dc.document_id, dc.chunk_index, dc.content,
                   d.filename, 1 - (dc.embedding <=> %s) AS similarity
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.embedding IS NOT NULL
    """
    params = [Vector(query_embedding)]
    if document_uuid is not None:
        sql += " AND dc.document_id = %s"
        params.append(document_uuid)
    sql += ") sub"
    if request.similarity_threshold is not None:
        sql += " WHERE similarity >= %s"
        params.append(request.similarity_threshold)
    sql += " ORDER BY similarity DESC LIMIT %s"
    params.append(request.top_k)

    try:
        rows = pg_conn.execute(sql, params).fetchall()
    except Exception as exc:
        print(f"[/documents/search] DB query failed: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while processing this search. Please try again.",
        )

    results = [
        SearchResult(
            chunk_id=str(chunk_id),
            document_id=str(row_document_id),
            chunk_index=chunk_index,
            content=content,
            filename=filename,
            similarity=similarity,
        )
        for chunk_id, row_document_id, chunk_index, content, filename, similarity in rows
    ]
    return SearchResponse(query=request.query, results=results)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
