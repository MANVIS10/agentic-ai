"""The Research Agent's node and subgraph, moved from
stage25_react_ui/backend/main.py (lines 133-153)."""

from langchain_core.messages import SystemMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agents.prompts import RESEARCH_SYSTEM_PROMPT
from app.llm import research_llm
from app.tools.web_search import search_web

research_tools = [search_web]

research_llm_with_tools = research_llm.bind_tools(research_tools)


async def research_agent_node(state: MessagesState):
    messages = [SystemMessage(content=RESEARCH_SYSTEM_PROMPT), *state["messages"]]
    response = await research_llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


research_subgraph_builder = StateGraph(MessagesState)
research_subgraph_builder.add_node("agent", research_agent_node)
research_subgraph_builder.add_node("tools", ToolNode(research_tools))
research_subgraph_builder.add_edge(START, "agent")
research_subgraph_builder.add_conditional_edges("agent", tools_condition)
research_subgraph_builder.add_edge("tools", "agent")

research_graph = research_subgraph_builder.compile()
