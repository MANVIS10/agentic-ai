"""The supervisor + critic graph and its three specialist-invoking node
wrappers, moved from stage25_react_ui/backend/main.py (lines 403-600).

knowledge_node applies the system-prompt-leak guard to whatever the
Knowledge Agent subgraph returns, before handing it back to the critic
(unchanged from the original).
"""

from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agents.analysis import analysis_graph
from app.agents.critic import critic_node, route_from_critic
from app.agents.knowledge import knowledge_graph
from app.agents.research import research_graph
from app.agents.supervisor import route_from_supervisor, supervisor_node
from app.config import LEAK_GUARD_FALLBACK_ANSWER
from app.security.leakguard import leaks_system_prompt


class CriticState(MessagesState):
    next: Literal["research", "knowledge", "analysis"]
    verdict: Literal["pass", "retry"]
    feedback: str
    retry_count: int
    user_id: str
    tools_used: list[str]  # new in Stage 25 (spec §3.2) - names of tools the
    # specialist subgraph invoked for the attempt that produced this state's
    # current answer, for the execution trace


async def research_node(state: CriticState):
    """Run the Research Agent subgraph and hand back only its final answer."""
    messages = state["messages"]
    if state.get("feedback"):
        messages = messages + [
            HumanMessage(
                content=f"Reviewer feedback: {state['feedback']} "
                "Please address this and try again."
            )
        ]
    result = await research_graph.ainvoke({"messages": messages})
    tool_names = [m.name for m in result["messages"] if isinstance(m, ToolMessage)]
    return {"messages": [result["messages"][-1]], "tools_used": tool_names}


async def knowledge_node(state: CriticState):
    """Run the Knowledge Agent subgraph and hand back only its final
    answer. Passes user_id into knowledge_graph's own state (KnowledgeState)
    so search_uploaded_documents's InjectedState argument can see it
    (unchanged from Stage 23). New in this stage: the returned answer is
    checked by leaks_system_prompt before being handed back - if a
    document somehow talked the model into reciting KNOWLEDGE_SYSTEM_PROMPT
    back, the real answer is logged server-side (never returned to the
    caller) and replaced with a safe fallback string (spec §7).
    """
    messages = state["messages"]
    if state.get("feedback"):
        messages = messages + [
            HumanMessage(
                content=f"Reviewer feedback: {state['feedback']} "
                "Please address this and try again."
            )
        ]
    result = await knowledge_graph.ainvoke({"messages": messages, "user_id": state["user_id"]})
    answer_message = result["messages"][-1]
    tool_names = [m.name for m in result["messages"] if isinstance(m, ToolMessage)]

    if isinstance(answer_message.content, str) and leaks_system_prompt(answer_message.content):
        print(
            "[knowledge_node] System-prompt-leak guard triggered; original "
            f"answer suppressed: {answer_message.content!r}"
        )
        answer_message = AIMessage(content=LEAK_GUARD_FALLBACK_ANSWER)

    return {"messages": [answer_message], "tools_used": tool_names}


async def analysis_node(state: CriticState):
    """Run the Analysis Agent subgraph and hand back only its final answer."""
    messages = state["messages"]
    if state.get("feedback"):
        messages = messages + [
            HumanMessage(
                content=f"Reviewer feedback: {state['feedback']} "
                "Please address this and try again."
            )
        ]
    result = await analysis_graph.ainvoke({"messages": messages})
    tool_names = [m.name for m in result["messages"] if isinstance(m, ToolMessage)]
    return {"messages": [result["messages"][-1]], "tools_used": tool_names}


supervisor_critic_builder = StateGraph(CriticState)
supervisor_critic_builder.add_node("supervisor", supervisor_node)
supervisor_critic_builder.add_node("research_agent", research_node)
supervisor_critic_builder.add_node("knowledge_agent", knowledge_node)
supervisor_critic_builder.add_node("analysis_agent", analysis_node)
supervisor_critic_builder.add_node("critic", critic_node)

supervisor_critic_builder.add_edge(START, "supervisor")
supervisor_critic_builder.add_conditional_edges(
    "supervisor",
    route_from_supervisor,
    {
        "research": "research_agent",
        "knowledge": "knowledge_agent",
        "analysis": "analysis_agent",
    },
)
supervisor_critic_builder.add_edge("research_agent", "critic")
supervisor_critic_builder.add_edge("knowledge_agent", "critic")
supervisor_critic_builder.add_edge("analysis_agent", "critic")
supervisor_critic_builder.add_conditional_edges(
    "critic",
    route_from_critic,
    {
        "research": "research_agent",
        "knowledge": "knowledge_agent",
        "analysis": "analysis_agent",
        "end": END,
    },
)

# No checkpointer here on purpose: this graph is invoked as a one-shot
# helper once per subtask (like Stage 8's research_agent), not run as its
# own multi-turn REPL the way Stage 16 runs it. Only the OUTER planner graph
# needs a checkpointer, for its one interrupt().
supervisor_critic_graph = supervisor_critic_builder.compile()
