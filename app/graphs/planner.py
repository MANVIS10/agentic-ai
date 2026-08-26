"""The outer planner + human-approval graph, moved from
stage25_react_ui/backend/main.py (lines 607-753).

One structural change from the original: it compiled the outer graph at
module scope (`graph = graph_builder.compile(checkpointer=checkpointer)`).
Here that's wrapped in `build_graph(checkpointer)` so importing this module
needs no database connection - the builder wiring itself is unchanged.
"""

from dataclasses import dataclass

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from app.config import MAX_REACT_STEPS
from app.graphs.specialist import supervisor_critic_graph
from app.llm import chat_llm


class PlannerState(TypedDict):
    question: str
    user_id: str
    subtasks: list[str]  # the plan the human approved - never mutated after
    # approval, so the API keeps reporting what was actually sanctioned
    agenda: list[str]  # work still outstanding; seeded from subtasks, and
    # extendable by reflect() with follow-ups the agent discovers it needs
    step_count: int  # guards against reflect() extending its own agenda
    # forever - see MAX_REACT_STEPS
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
        # The approved plan seeds the executor's agenda rather than being a
        # fixed script - that is the whole difference between the old
        # current_index walk and the ReAct loop.
        "agenda": list(subtasks),
        "step_count": 0,
        "results": [],
        "final_answer": "",
        "approved": False,
        "trace": [],
    }


def human_approval(state: PlannerState):
    decision = interrupt("Approve this plan? (y/n): ")
    return {"approved": decision.strip().lower() == "y"}


def route_after_approval(state: PlannerState) -> str:
    if not state["approved"]:
        return END
    # Don't assume plan() produced at least one subtask - decide_next_action
    # sends an empty agenda straight to synthesize rather than popping from
    # an empty list.
    return route_from_decision(state)


def route_from_decision(state: PlannerState) -> str:
    """Maps the pure decision onto a graph edge. Both the post-approval entry
    and the loop's back-edge route through here, so the step budget applies
    to every path into react_step - there is no way around the ceiling."""
    return "react_step" if decide_next_action(state).action == "research" else "synthesize"


# Stands in for a subtask whose research raised. Deliberately generic and
# free of any exception detail: it reaches the caller both in the synthesis
# prompt and, via results, in the API response.
SUBTASK_FAILED_PLACEHOLDER = "Could not complete this subtask due to an internal error."


def build_failed_trace_entry(subtask: str, origin: str = "approved") -> dict:
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
        "origin": origin,
    }


@dataclass(frozen=True)
class ReactDecision:
    """What the executor loop does next. `reason` is recorded so a run that
    stopped early is distinguishable from one that finished its work."""

    action: str  # "research" | "finish"
    subtask: str | None = None
    reason: str = ""


def decide_next_action(state: dict) -> ReactDecision:
    """Pure control-flow decision for the ReAct loop - deliberately no LLM
    call.

    The model reasons about RESULTS in reflect(); it never decides when the
    loop stops. Keeping termination deterministic is what makes the loop
    auditable and testable: an agent that chooses its own exit condition
    cannot be reasoned about, and reflect() can extend its own agenda.

    The budget is checked BEFORE the agenda, so a full agenda can never
    outvote the ceiling.
    """
    if state["step_count"] >= MAX_REACT_STEPS:
        return ReactDecision(action="finish", reason="step_budget_exhausted")
    if not state["agenda"]:
        return ReactDecision(action="finish", reason="agenda_empty")
    return ReactDecision(action="research", subtask=state["agenda"][0], reason="work_remaining")


def build_trace_entry(subtask: str, result: dict, origin: str = "approved") -> dict:
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
        # "approved" = in the plan the human sanctioned; "agent" = added by
        # reflect() mid-run. Without this the approval gate is decorative.
        "origin": origin,
    }


async def react_step(state: PlannerState):
    """Research one subtask by running it through the full
    supervisor -> specialist -> critic pipeline, instead of a bare LLM call
    (Stage 6/7) or a single flat tool agent (Stage 8).

    Each subtask gets a fresh invocation - no shared thread/state carries
    over between subtasks, so retry_count always starts at 0 for each one.
    user_id is threaded into the inner graph's own state here so it can
    reach knowledge_node -> knowledge_graph -> search_uploaded_documents's
    InjectedState argument (unchanged from Stage 23).
    """
    subtask = state["agenda"][0]
    remaining = state["agenda"][1:]
    # A subtask counts as "approved" only if it appeared in the plan the
    # human actually saw; anything reflect() added mid-run is "agent".
    origin = "approved" if subtask in state["subtasks"] else "agent"
    print(f"\nResearching ({origin}): {subtask}")

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
        print(f"[react_step] {type(exc).__name__} on subtask {subtask!r}: {exc}")
        return {
            "results": state["results"] + [SUBTASK_FAILED_PLACEHOLDER],
            "trace": state["trace"] + [build_failed_trace_entry(subtask, origin=origin)],
            # Consume the item and spend the step even on failure, or the
            # loop re-dispatches the same failing subtask until the budget
            # runs out.
            "agenda": remaining,
            "step_count": state["step_count"] + 1,
        }

    print(f"  [Supervisor routed to: {result['next']}]")
    print(f"  [Critic verdict: {result['verdict']}, retries used: {result['retry_count']}]")

    answer = result["messages"][-1].content

    trace_entry = build_trace_entry(subtask, result, origin=origin)

    return {
        "results": state["results"] + [answer],
        "trace": state["trace"] + [trace_entry],
        "agenda": remaining,
        "step_count": state["step_count"] + 1,
    }


FOLLOW_UP_NONE = "NONE"

REFLECT_PROMPT = (
    "You are tracking progress on a research question.\n\n"
    "Question: {question}\n\n"
    "Findings so far:\n{findings}\n\n"
    "Still queued: {agenda}\n\n"
    "If these findings leave a genuine gap that must be researched to answer "
    "the question, reply with ONE short, concrete follow-up subtask and "
    "nothing else. If the question can be answered with what you have, or the "
    f"only gaps are minor, reply with exactly {FOLLOW_UP_NONE}.\n"
    "Do not repeat a subtask that is already queued or already answered."
)


async def reflect(state: PlannerState):
    """The 'observe' half of the loop: look at what's been found and decide
    whether a follow-up subtask is needed.

    This is where the LLM belongs - reasoning about RESULTS. It cannot end
    the loop or skip work: it may only append to the agenda, and
    decide_next_action still governs termination. That split is what keeps
    the loop bounded and auditable.

    A follow-up is appended, never substituted, so the human-approved
    subtasks are always executed.
    """
    if not state["agenda"] and state["step_count"] < MAX_REACT_STEPS:
        findings = "\n\n".join(
            f"- {entry['subtask']}: {result}"
            for entry, result in zip(state["trace"], state["results"])
        )
        response = await chat_llm.ainvoke(
            REFLECT_PROMPT.format(
                question=state["question"],
                findings=findings or "(nothing yet)",
                agenda=", ".join(state["agenda"]) or "(nothing)",
            )
        )
        follow_up = response.content.strip()
        already_seen = {e["subtask"] for e in state["trace"]} | set(state["agenda"])
        if follow_up and follow_up.upper() != FOLLOW_UP_NONE and follow_up not in already_seen:
            print(f"  [reflect] added follow-up: {follow_up}")
            return {"agenda": state["agenda"] + [follow_up]}

    return {}


async def synthesize(state: PlannerState):
    # Pair against the TRACE, not against subtasks: the loop may have
    # researched a follow-up that was never in the approved plan, so
    # zip(subtasks, results) would silently drop it - or, worse, mis-pair
    # every answer after the first follow-up. trace and results are appended
    # in lockstep by react_step, so they always align.
    subtasks_and_results = "\n\n".join(
        f"Subtask: {entry['subtask']}\nAnswer: {result}"
        for entry, result in zip(state["trace"], state["results"])
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
    graph_builder.add_node("react_step", react_step)
    graph_builder.add_node("reflect", reflect)
    graph_builder.add_node("synthesize", synthesize)

    graph_builder.add_edge(START, "plan")
    graph_builder.add_edge("plan", "human_approval")
    graph_builder.add_conditional_edges("human_approval", route_after_approval)
    # act -> observe -> decide. Every path back into react_step goes through
    # route_from_decision, so the step budget cannot be bypassed.
    graph_builder.add_edge("react_step", "reflect")
    graph_builder.add_conditional_edges("reflect", route_from_decision)
    graph_builder.add_edge("synthesize", END)

    return graph_builder.compile(checkpointer=checkpointer)
