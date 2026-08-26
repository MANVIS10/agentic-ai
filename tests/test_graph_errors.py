"""Phase 3A Task 2: a failing subtask must not destroy the whole run.

Before this, an exception raised by a specialist or a tool propagated out of
`supervisor_critic_graph.ainvoke()`, through `research_subtask`, out of the
outer graph, and into the 500 handler - discarding every already-completed
subtask's work despite the checkpointer. One flaky web search destroyed a
three-subtask run.
"""

import pytest

from app.graphs import planner


class _Msg:
    def __init__(self, content):
        self.content = content


def _ok_result():
    return {
        "messages": [_Msg("an answer")],
        "next": "research",
        "verdict": "pass",
        "retry_count": 0,
        "tools_used": ["search_web"],
    }


async def test_successful_subtask_is_unaffected(monkeypatch):
    async def fine(*a, **kw):
        return _ok_result()

    monkeypatch.setattr(planner.supervisor_critic_graph, "ainvoke", fine)
    out = await planner.research_subtask(
        {"subtasks": ["a"], "current_index": 0, "user_id": "u", "results": [], "trace": []}
    )
    assert out["trace"][0]["status"] == "completed"
    assert out["results"] == ["an answer"]


async def test_raising_specialist_becomes_a_recorded_failure(monkeypatch):
    """The run continues with the failure recorded, rather than aborting."""

    async def boom(*a, **kw):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(planner.supervisor_critic_graph, "ainvoke", boom)
    out = await planner.research_subtask(
        {"subtasks": ["a"], "current_index": 0, "user_id": "u", "results": [], "trace": []}
    )

    assert out["trace"][0]["status"] == "failed"
    assert out["current_index"] == 1, "the loop must still advance, or it spins forever"
    assert len(out["results"]) == 1, "a placeholder answer keeps subtasks and results aligned"


async def test_failure_never_leaks_exception_text(monkeypatch):
    """Same error-hygiene rule the HTTP layer already enforces: an exception
    message can carry a connection string or a prompt fragment."""

    async def boom(*a, **kw):
        raise RuntimeError("DATABASE_URL=postgresql://postgres:postgres@host/db")

    monkeypatch.setattr(planner.supervisor_critic_graph, "ainvoke", boom)
    out = await planner.research_subtask(
        {"subtasks": ["a"], "current_index": 0, "user_id": "u", "results": [], "trace": []}
    )

    serialized = str(out)
    assert "postgres:postgres@" not in serialized
    assert "DATABASE_URL" not in serialized
    assert "upstream exploded" not in serialized


async def test_synthesize_marks_failed_subtasks_for_the_llm(monkeypatch):
    """A placeholder must not be presented to the model as a research
    finding, or the final answer will confidently report a failure as fact."""
    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            return _Msg("final")

    monkeypatch.setattr(planner, "chat_llm", _FakeLLM())
    await planner.synthesize(
        {
            "question": "q",
            "subtasks": ["a", "b"],
            "results": ["a real answer", planner.SUBTASK_FAILED_PLACEHOLDER],
        }
    )
    assert planner.SUBTASK_FAILED_PLACEHOLDER in captured["prompt"]
