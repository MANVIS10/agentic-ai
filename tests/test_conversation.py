"""A conversational branch in front of the planner.

Before this, every /chat message went straight to plan(): "I'm Manvi"
produced an invented 2-3 subtask research plan and an approval prompt for
a plan nobody asked for. classify() now decides first - small talk is
answered by greet(), research still goes to plan() and the approval gate.

Two properties here are safety properties, not conveniences:
  - an unclear intent must fall back to RESEARCH, so a real question can
    never be swallowed by a chatty non-answer;
  - greet() must clear the previous turn's research state, or /chat
    reports a stale plan alongside the greeting.
"""

import inspect

from app.graphs.planner import classify, greet, plan, route_after_classify


class _FakeIntent:
    """Stands in for llm.intent_llm, whose .with_structured_output(Intent)
    wrapper returns a plain dict rather than a message."""

    def __init__(self, intent):
        self.intent = intent
        self.prompt = None

    async def ainvoke(self, prompt, *a, **kw):
        self.prompt = prompt
        return self.intent


class _FakeChat:
    """Stands in for llm.chat_llm, which returns a message with .content."""

    def __init__(self, content="hello there"):
        self.content = content
        self.prompt = None

    async def ainvoke(self, prompt, *a, **kw):
        self.prompt = prompt
        return type("Msg", (), {"content": self.content})()


def _state(**overrides):
    state = {
        "question": "I'm Manvi",
        "user_id": "u1",
        "user_name": "",
        "intent": "",
        "subtasks": [],
        "agenda": [],
        "step_count": 0,
        "results": [],
        "final_answer": "",
        "approved": False,
        "trace": [],
    }
    state.update(overrides)
    return state


def test_small_talk_routes_to_greet():
    assert route_after_classify(_state(intent="chat")) == "greet"


def test_a_research_question_still_routes_to_plan():
    assert route_after_classify(_state(intent="research")) == "plan"


def test_an_unrecognised_intent_falls_back_to_research():
    """The asymmetric failure: a question mistaken for small talk gets a
    friendly non-answer, while small talk mistaken for a question merely
    costs a plan. Anything that isn't explicitly "chat" must reach plan()."""
    assert route_after_classify(_state(intent="")) == "plan"
    assert route_after_classify(_state(intent="something-else")) == "plan"


async def test_classify_records_the_intent_and_the_name(monkeypatch):
    from app.graphs import planner

    monkeypatch.setattr(
        planner, "intent_llm", _FakeIntent({"kind": "chat", "user_name": "Manvi"})
    )
    update = await classify(_state(question="hi, I'm Manvi"))
    assert update["intent"] == "chat"
    assert update["user_name"] == "Manvi"


async def test_a_name_survives_a_message_that_doesnt_repeat_it(monkeypatch):
    """"I'm Manvi" then "what's in my PDF?" - the second message carries no
    name, and must not erase the one already learned on this thread."""
    from app.graphs import planner

    monkeypatch.setattr(
        planner, "intent_llm", _FakeIntent({"kind": "research", "user_name": ""})
    )
    update = await classify(_state(question="what's in my PDF?", user_name="Manvi"))
    assert update["user_name"] == "Manvi"


async def test_greet_addresses_the_user_by_name(monkeypatch):
    from app.graphs import planner

    fake = _FakeChat("Nice to meet you, Manvi!")
    monkeypatch.setattr(planner, "chat_llm", fake)
    update = await greet(_state(user_name="Manvi"))
    assert "Manvi" in fake.prompt
    assert update["final_answer"] == "Nice to meet you, Manvi!"


async def test_greet_clears_the_previous_turns_research_state(monkeypatch):
    """A thread that researched something and is then greeted must not
    report the old plan back: /chat returns subtasks and trace straight
    out of state."""
    from app.graphs import planner

    monkeypatch.setattr(planner, "chat_llm", _FakeChat())
    update = await greet(
        _state(
            subtasks=["old subtask"],
            agenda=["old subtask"],
            results=["old result"],
            trace=[{"subtask": "old subtask"}],
            approved=True,
            step_count=3,
        )
    )
    assert update["subtasks"] == []
    assert update["agenda"] == []
    assert update["results"] == []
    assert update["trace"] == []
    assert update["approved"] is False
    assert update["step_count"] == 0


async def test_plan_does_not_reset_the_learned_name(monkeypatch):
    """plan() deliberately resets every field a later node might set. The
    name is not one of them - it belongs to the conversation, not the turn -
    and LangGraph only overwrites keys a node actually returns."""
    from app.graphs import planner

    monkeypatch.setattr(planner, "chat_llm", _FakeChat("a\nb"))
    update = await plan(_state(question="what is pgvector?", user_name="Manvi"))
    assert "user_name" not in update


def test_the_graph_wires_the_conversational_branch():
    from app.graphs.planner import build_graph

    nodes = set(build_graph(None).get_graph().nodes)
    assert {"classify", "greet"} <= nodes
    assert {"plan", "human_approval", "react_step", "reflect", "synthesize"} <= nodes


def test_the_new_nodes_are_coroutines():
    """A sync node inside an async graph silently blocks the event loop."""
    assert inspect.iscoroutinefunction(classify)
    assert inspect.iscoroutinefunction(greet)
