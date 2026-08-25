import inspect

from app.graphs.planner import has_more_subtasks
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
    assert has_more_subtasks({"subtasks": ["a", "b"], "current_index": 0}) == "research_subtask"
    assert has_more_subtasks({"subtasks": ["a", "b"], "current_index": 2}) == "synthesize"


def test_empty_plan_goes_straight_to_synthesize():
    assert has_more_subtasks({"subtasks": [], "current_index": 0}) == "synthesize"


def test_all_graph_nodes_are_coroutines():
    """A sync node inside an async graph silently blocks the event loop -
    the failure mode is a latency cliff under load, not an error, so assert
    it structurally."""
    from app.agents import critic, supervisor
    from app.graphs import planner

    for fn in (
        planner.plan,
        planner.synthesize,
        planner.research_subtask,
        supervisor.supervisor_node,
        critic.critic_node,
    ):
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} is still sync"
