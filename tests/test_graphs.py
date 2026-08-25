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
