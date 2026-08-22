"""
Stage 11: the same tool-calling agent as Stage 2, given an explicit
identity - a "Research Agent" - via a system prompt.

New concept vs. Stage 1-10:
- A specialist agent isn't a new LangGraph mechanism. It's Stage 2's exact
  `agent -> tools -> agent` loop (bind_tools, ToolNode, tools_condition,
  MemorySaver) with one thing added: a SystemMessage that tells the LLM
  what its job is and what tool it has, before every turn.
- That system prompt is what "specializes" the agent - it narrows the
  agent's declared role and scope down to one job (web research), rather
  than leaving it a generic tool-user with no stated purpose.
- Contrast with Stage 10: Stage 10 is one agent with FOUR unrelated tools
  and no declared role - it's a generalist that picks whichever tool fits.
  Stage 11 is one agent with ONE tool and an explicit identity - it's a
  specialist. Same graph shape, different framing, and that framing is the
  whole point: it's the building block a future supervisor/multi-agent
  system would route work to.

No new tools were created - search_web is duplicated verbatim from Stage 2
(self-contained stage, no shared `common/` module).
"""

from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()

SYSTEM_PROMPT = (
    "You are a Research Agent, a specialist whose only job is web research. "
    "You have one tool: web search. When asked something you don't already "
    "know for certain, search the web for it before answering. Report what "
    "you found clearly and cite that it came from a web search when you use "
    "it. Stay focused on research - you're not a general-purpose assistant."
)

search_web = DuckDuckGoSearchRun()
tools = [search_web]

llm = ChatOpenAI(model="gpt-4o-mini")
llm_with_tools = llm.bind_tools(tools)


def agent(state: MessagesState):
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


graph_builder = StateGraph(MessagesState)
graph_builder.add_node("agent", agent)
graph_builder.add_node("tools", ToolNode(tools))

graph_builder.add_edge(START, "agent")
graph_builder.add_conditional_edges("agent", tools_condition)
graph_builder.add_edge("tools", "agent")

graph = graph_builder.compile(checkpointer=MemorySaver())


def main():
    config = {"configurable": {"thread_id": "1"}}
    print("Stage 11 research agent. Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break

        result = graph.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=config,
        )
        print(f"Bot: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
