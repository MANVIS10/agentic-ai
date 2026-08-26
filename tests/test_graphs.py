import inspect

from app.graphs.planner import route_after_approval, route_from_decision
from app.graphs.specialist import supervisor_critic_graph


def test_specialist_graph_topology_is_unchanged():
    nodes = set(supervisor_critic_graph.get_graph().nodes)
    assert {
        "supervisor",
        "research_agent",
        "knowledge_agent",
        "analysis_agent",
        "critic",
    } <= nodes


def test_subtask_loop_advances_and_terminates():
    """Same property the pre-ReAct `has_more_subtasks` guarded: work
    outstanding routes to the executor, no work routes to synthesis."""
    assert route_from_decision({"agenda": ["a", "b"], "step_count": 0}) == "react_step"
    assert route_from_decision({"agenda": [], "step_count": 2}) == "synthesize"


def test_empty_plan_goes_straight_to_synthesize():
    assert route_from_decision({"agenda": [], "step_count": 0}) == "synthesize"


def test_rejected_plan_ends_without_researching():
    """Rejection must reach END, never the executor - otherwise the approval
    gate does nothing."""
    from langgraph.graph import END

    assert route_after_approval(
        {"approved": False, "agenda": ["a"], "step_count": 0}
    ) == END


def test_approved_plan_enters_the_executor():
    assert (
        route_after_approval({"approved": True, "agenda": ["a"], "step_count": 0})
        == "react_step"
    )


def test_planner_graph_wires_the_react_loop():
    """act -> observe, then a decision edge back or on to synthesis."""
    from app.graphs.planner import build_graph

    nodes = set(build_graph(None).get_graph().nodes)
    assert {"plan", "human_approval", "react_step", "reflect", "synthesize"} <= nodes


def test_all_graph_nodes_are_coroutines():
    """A sync node inside an async graph silently blocks the event loop -
    the failure mode is a latency cliff under load, not an error, so assert
    it structurally."""
    from app.agents import critic, supervisor
    from app.graphs import planner

    for fn in (
        planner.plan,
        planner.synthesize,
        planner.react_step,
        planner.reflect,
        supervisor.supervisor_node,
        critic.critic_node,
    ):
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} is still sync"
