"""The Knowledge Agent's state, node, and subgraph, moved from
stage25_react_ui/backend/main.py (lines 203-281)."""

from langchain_core.messages import SystemMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agents.prompts import KNOWLEDGE_SYSTEM_PROMPT
from app.llm import knowledge_llm
from app.tools.document_search import search_uploaded_documents


class KnowledgeState(MessagesState):
    """MessagesState plus user_id - the one piece of trusted context this
    subgraph's tool needs but the LLM must never control. Only the
    Knowledge Agent subgraph needs this; Research and Analysis don't touch
    documents, so their subgraphs stay plain MessagesState (unchanged from
    Stage 23).
    """

    user_id: str


knowledge_tools = [search_uploaded_documents]

knowledge_llm_with_tools = knowledge_llm.bind_tools(knowledge_tools)


async def knowledge_agent_node(state: KnowledgeState):
    messages = [SystemMessage(content=KNOWLEDGE_SYSTEM_PROMPT), *state["messages"]]
    response = await knowledge_llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


knowledge_subgraph_builder = StateGraph(KnowledgeState)
knowledge_subgraph_builder.add_node("agent", knowledge_agent_node)
knowledge_subgraph_builder.add_node("tools", ToolNode(knowledge_tools))
knowledge_subgraph_builder.add_edge(START, "agent")
knowledge_subgraph_builder.add_conditional_edges("agent", tools_condition)
knowledge_subgraph_builder.add_edge("tools", "agent")

knowledge_graph = knowledge_subgraph_builder.compile()
