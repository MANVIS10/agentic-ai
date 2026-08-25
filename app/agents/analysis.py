"""The Analysis Agent's node and subgraph, moved from
stage25_react_ui/backend/main.py (lines 362-393)."""

from langchain_core.messages import SystemMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agents.prompts import ANALYSIS_SYSTEM_PROMPT
from app.llm import analysis_llm
from app.tools.calculator import calculate

analysis_tools = [calculate]

analysis_llm_with_tools = analysis_llm.bind_tools(analysis_tools)


async def analysis_agent_node(state: MessagesState):
    messages = [SystemMessage(content=ANALYSIS_SYSTEM_PROMPT), *state["messages"]]
    response = await analysis_llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


analysis_subgraph_builder = StateGraph(MessagesState)
analysis_subgraph_builder.add_node("agent", analysis_agent_node)
analysis_subgraph_builder.add_node("tools", ToolNode(analysis_tools))
analysis_subgraph_builder.add_edge(START, "agent")
analysis_subgraph_builder.add_conditional_edges("agent", tools_condition)
analysis_subgraph_builder.add_edge("tools", "agent")

analysis_graph = analysis_subgraph_builder.compile()
