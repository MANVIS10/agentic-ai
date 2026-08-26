"""The outer planner + human-approval graph, moved from
stage25_react_ui/backend/main.py (lines 607-753).

One structural change from the original: it compiled the outer graph at
module scope (`graph = graph_builder.compile(checkpointer=checkpointer)`).
Here that's wrapped in `build_graph(checkpointer)` so importing this module
needs no database connection - the builder wiring itself is unchanged.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from app.graphs.specialist import supervisor_critic_graph
from app.llm import chat_llm


class PlannerState(TypedDict):
    question: str
    user_id: str
    subtasks: list[str]
    current_index: int
    results: list[str]
    final_answer: str
    approved: bool
    trace: list[dict]  # new in Stage 25 (spec §3.2) - one entry per
    # completed subtask (plain dicts, not SubtaskTrace - that stays a
    # pure HTTP-layer response type, matching how graph state elsewhere
    # in this file is always a TypedDict/plain dict, never a BaseModel)


async def plan(state: PlannerState):
    prompt = (
        "Break the following research question into 2-3 short, concrete "
        "subtasks that could each be researched independently. "
        "Reply with just the subtasks, one per line, no numbering.\n\n"
        f"Question: {state['question']}"
    )
    response = await chat_llm.ainvoke(prompt)
    subtasks = [line.strip() for line in response.content.splitlines() if line.strip()]

    print(f"\nPlan ({len(subtasks)} subtasks):")
    for i, subtask in enumerate(subtasks, start=1):
        print(f"  {i}. {subtask}")

    # Reset every field a later node might set, not just the ones this node
    # itself uses. plan() is the one node guaranteed to run first on every
    # turn (START -> plan), so it's the only place that can undo a stale
    # final_answer/approved left behind by an earlier question on the same
    # thread. user_id isn't reset here, same treatment as `question` - both
    # arrive fresh as graph.invoke() input on every /chat call.
    return {
        "subtasks": subtasks,
        "current_index": 0,
        "results": [],
        "final_answer": "",
        "approved": False,
        "trace": [],
    }


def human_approval(state: PlannerState):
    decision = interrupt("Approve this plan? (y/n): ")
    return {"approved": decision.strip().lower() == "y"}


def has_more_subtasks(state: PlannerState) -> str:
    if state["current_index"] < len(state["subtasks"]):
        return "research_subtask"
    return "synthesize"


def route_after_approval(state: PlannerState) -> str:
    if not state["approved"]:
        return END
    # Don't assume plan() produced at least one subtask - reuse the same
    # "anything left to research?" check used between subtask loops, so an
    # empty plan goes straight to synthesize instead of indexing into an
    # empty subtasks list in research_subtask.
    return has_more_subtasks(state)


# Stands in for a subtask whose research raised. Deliberately generic and
# free of any exception detail: it reaches the caller both in the synthesis
# prompt and, via results, in the API response.
SUBTASK_FAILED_PLACEHOLDER = "Could not complete this subtask due to an internal error."


def build_failed_trace_entry(subtask: str) -> dict:
    """Trace record for a subtask whose research raised.

    Mirrors build_trace_entry's shape so the UI needs no special case, but
    carries no specialist or tool attribution - when the dispatch itself
    raised, we genuinely do not know which specialist would have handled it.
    """
    return {
        "subtask": subtask,
        "specialist": "unknown",
        "tools_used": [],
        "status": "failed",
        "verdict": "retry",
        "retry_count": 0,
    }


def build_trace_entry(subtask: str, result: dict) -> dict:
    """One subtask's trace record (Stage 25 spec §3.2), derived from the
    inner supervisor+critic graph's final state.

    `status` is derived from the critic's verdict rather than hardcoded.
    Phase 1 carried the original's literal `"completed"` forward for every
    subtask, including one whose critic said "retry" and then exhausted
    MAX_RETRIES without ever accepting the answer - so the UI reported a
    clean pass for something the system had rejected. A verdict still
    reading "retry" here means exactly that: the retry budget ran out,
    the last attempt was returned anyway, and a human should look at it.

    On a retry, the specialist node's return value (including tools_used)
    is overwritten in CriticState before critic_node re-runs, the same way
    verdict/next already work - so this entry reflects the final attempt.
    """
    return {
        "subtask": subtask,
        "specialist": result["next"],
        "tools_used": result.get("tools_used", []),
        "status": "completed" if result["verdict"] == "pass" else "needs_review",
        "verdict": result["verdict"],
        "retry_count": result["retry_count"],
    }


async def research_subtask(state: PlannerState):
    """Research one subtask by running it through the full
    supervisor -> specialist -> critic pipeline, instead of a bare LLM call
    (Stage 6/7) or a single flat tool agent (Stage 8).

    Each subtask gets a fresh invocation - no shared thread/state carries
    over between subtasks, so retry_count always starts at 0 for each one.
    user_id is threaded into the inner graph's own state here so it can
    reach knowledge_node -> knowledge_graph -> search_uploaded_documents's
    InjectedState argument (unchanged from Stage 23).
    """
    subtask = state["subtasks"][state["current_index"]]
    print(f"\nResearching: {subtask}")

    # A specialist or tool raising must not abort the run. Before this, the
    # exception propagated out of the outer graph to the 500 handler and
    # every already-completed subtask's work was discarded - despite the
    # checkpointer - so one flaky web search destroyed a three-subtask run.
    try:
        result = await supervisor_critic_graph.ainvoke(
            {"messages": [{"role": "user", "content": subtask}], "user_id": state["user_id"]}
        )
    except Exception as exc:
        # Logged server-side only. exc's text can carry a connection string
        # or a prompt fragment, so it must never reach the caller - the same
        # rule the HTTP layer's unhandled_exception_handler already applies.
        print(f"[research_subtask] {type(exc).__name__} on subtask {subtask!r}: {exc}")
        return {
            "results": state["results"] + [SUBTASK_FAILED_PLACEHOLDER],
            "trace": state["trace"] + [build_failed_trace_entry(subtask)],
            # Still advance, or has_more_subtasks re-dispatches the same
            # failing subtask forever.
            "current_index": state["current_index"] + 1,
        }

    print(f"  [Supervisor routed to: {result['next']}]")
    print(f"  [Critic verdict: {result['verdict']}, retries used: {result['retry_count']}]")

    answer = result["messages"][-1].content

    trace_entry = build_trace_entry(subtask, result)

    return {
        "results": state["results"] + [answer],
        "trace": state["trace"] + [trace_entry],
        "current_index": state["current_index"] + 1,
    }


async def synthesize(state: PlannerState):
    subtasks_and_results = "\n\n".join(
        f"Subtask: {subtask}\nAnswer: {result}"
        for subtask, result in zip(state["subtasks"], state["results"])
    )
    prompt = (
        f"Original question: {state['question']}\n\n"
        f"Research notes:\n{subtasks_and_results}\n\n"
        "Combine these into one clear final answer to the original question.\n"
        # Without this, a failed subtask's placeholder reads to the model as
        # a research finding, and the final answer states the failure as if
        # it were something the research established.
        f'If a subtask\'s answer is "{SUBTASK_FAILED_PLACEHOLDER}", that '
        "subtask could not be researched - do not treat it as a finding. Say "
        "plainly which part of the question you could not cover, and answer "
        "the rest."
    )
    response = await chat_llm.ainvoke(prompt)
    return {"final_answer": response.content}


def build_graph(checkpointer: AsyncPostgresSaver) -> CompiledStateGraph:
    """Builds and compiles the outer planner graph against the given
    checkpointer. A function instead of the original's module-scope
    `graph = graph_builder.compile(checkpointer=checkpointer)` so importing
    this module needs no database connection - the wiring itself is
    unchanged from the original.
    """
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

    return graph_builder.compile(checkpointer=checkpointer)
