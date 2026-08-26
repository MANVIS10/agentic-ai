"""Phase 3A Task 3: a bounded ReAct executor loop.

The fixed `current_index` walk executed exactly the approved subtasks, in
order, and stopped. The loop replaces that with reason -> act -> observe:
after each subtask the agent reflects on what it has, and may finish early
or add a follow-up subtask it discovers it needs.

Human approval is deliberately unchanged. A pure ReAct agent has no plan to
approve - the plan emerges as it goes - which would break /chat, /approve,
/reject and the frontend's approval panel. So the approved subtasks SEED the
loop's agenda instead of being a script.

Two properties here are safety properties, not conveniences:
  - the step budget is hard, because reflect() can extend its own agenda;
  - trace `origin` distinguishes human-approved subtasks from agent-added
    ones, or the approval gate is decorative.
"""

from app.config import MAX_REACT_STEPS
from app.graphs.planner import decide_next_action


def _state(agenda, step_count=0, results=None):
    return {
        "question": "q",
        "agenda": agenda,
        "results": results or [],
        "step_count": step_count,
    }


def test_finishes_when_the_agenda_is_empty():
    decision = decide_next_action(_state([]))
    assert decision.action == "finish"
    assert decision.reason == "agenda_empty"


def test_continues_while_work_remains():
    decision = decide_next_action(_state(["a", "b"]))
    assert decision.action == "research"
    assert decision.subtask == "a"


def test_step_budget_is_hard():
    """reflect() can append to the agenda, so without a ceiling an ambiguous
    question loops until the request times out. The budget must win even
    with work outstanding."""
    decision = decide_next_action(_state(["still", "more"], step_count=MAX_REACT_STEPS))
    assert decision.action == "finish"
    assert decision.reason == "step_budget_exhausted"


def test_budget_is_checked_before_the_agenda():
    """An exhausted budget finishes even on a full agenda - the ordering that
    makes the ceiling meaningful."""
    decision = decide_next_action(_state(["a"] * 50, step_count=MAX_REACT_STEPS + 5))
    assert decision.action == "finish"


def test_decide_next_action_makes_no_llm_call(monkeypatch):
    """Loop termination must stay deterministic and auditable. The LLM
    reasons about RESULTS in reflect(); it never decides when to stop."""
    from app.graphs import planner

    class _Explode:
        async def ainvoke(self, *a, **kw):
            raise AssertionError("decide_next_action must not call the LLM")

    monkeypatch.setattr(planner, "chat_llm", _Explode())
    assert decide_next_action(_state(["a"])).action == "research"
    assert decide_next_action(_state([])).action == "finish"


def test_approved_and_agent_added_subtasks_are_distinguishable():
    """If the loop can add subtasks after a human approved a plan, and the
    trace cannot tell them apart, the approval gate means nothing - the
    agent could research anything and the record would look identical.
    """
    from app.graphs.planner import build_trace_entry

    result = {"next": "research", "verdict": "pass", "retry_count": 0, "tools_used": []}
    assert build_trace_entry("s", result, origin="approved")["origin"] == "approved"
    assert build_trace_entry("s", result, origin="agent")["origin"] == "agent"


def test_trace_origin_defaults_to_approved():
    """Callers that predate the loop (and the failure path) must not silently
    mark a human-approved subtask as agent-added."""
    from app.graphs.planner import build_failed_trace_entry, build_trace_entry

    result = {"next": "research", "verdict": "pass", "retry_count": 0, "tools_used": []}
    assert build_trace_entry("s", result)["origin"] == "approved"
    assert build_failed_trace_entry("s")["origin"] == "approved"
