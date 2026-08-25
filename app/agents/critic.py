"""The critic's review node, moved from stage25_react_ui/backend/main.py
(lines 519-561). `Review` is defined in app.llm, not here - see
app/llm.py's module docstring for why - re-exported here so
`app.agents.critic.Review` still resolves.

`CriticState` lives in graphs/specialist.py, downstream of agents; see
app/agents/supervisor.py's docstring for why `state` is typed as a plain
`dict` here instead (no behavior change - TypedDicts aren't enforced at
runtime).
"""

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.prompts import CRITIC_SYSTEM_PROMPT
from app.config import MAX_RETRIES
from app.llm import Review, critic_llm

__all__ = ["Review", "critic_node", "route_from_critic"]


def critic_node(state: dict):
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


def route_from_critic(state: dict) -> str:
    if state["verdict"] == "pass":
        return "end"
    return state["next"]
