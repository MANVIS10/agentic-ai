"""The supervisor's routing node, moved from
stage25_react_ui/backend/main.py (lines 414-450). `Route` is defined in
app.llm, not here - see app/llm.py's module docstring for why (supervisor_llm
needs it to build its `.with_structured_output(...)` call, and llm is
upstream of agents in this plan's import direction) - re-exported here so
`app.agents.supervisor.Route` still resolves.

`CriticState` (the state type `state` is shaped like) lives in
graphs/specialist.py, downstream of agents - importing it here would cycle
back (graphs already imports this module for supervisor_node). TypedDicts
are structural, not enforced at runtime, so `state` is typed as a plain
`dict` here with no behavior change; the original's `state: CriticState`
annotation was for readability only.
"""

from langchain_core.messages import SystemMessage

from app.agents.prompts import SUPERVISOR_SYSTEM_PROMPT
from app.llm import Route, supervisor_llm

__all__ = ["Route", "supervisor_node", "route_from_supervisor"]


async def supervisor_node(state: dict):
    messages = [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT), *state["messages"]]
    route: Route = await supervisor_llm.ainvoke(messages)
    return {"next": route["next"], "retry_count": 0}


def route_from_supervisor(state: dict) -> str:
    return state["next"]
