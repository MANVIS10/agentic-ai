"""
Stage 15: a third specialist agent, alongside Stage 11's Research Agent and
Stage 12's Knowledge Agent - an Analysis Agent specialized in calculations,
comparisons, and reasoning over numeric/structured data.

New concept vs. Stage 1-14:
- Nothing new mechanically. This is Stage 11's exact pattern (`bind_tools` +
  `ToolNode` + `tools_condition` loop, one tool, a `SystemMessage` identity,
  its own `MemorySaver`) stamped out a third time with a different tool and
  role - same move Stage 12 made for the Knowledge Agent.
- What's different is the *kind* of tool: Research and Knowledge both
  retrieve information (web search / local documents). The Analysis Agent's
  tool doesn't retrieve anything - it computes. It takes numbers the agent
  already has (from the conversation) and does arithmetic on them reliably,
  since LLMs are not trustworthy at doing math in their heads.

calculate:
- A single arithmetic-expression tool, e.g. "(12 + 18 + 30) / 3" or
  "((120 - 100) / 100) * 100". Safe by construction: the expression is
  parsed with Python's `ast` module and walked by hand, allowing only
  numeric literals and the operators +, -, *, /, //, %, ** (plus unary
  +/-). No `eval()`, no names, no function calls, no attribute access - so
  there's nothing to sandbox-escape. This is intentionally the simplest
  possible "calculator" tool, not a data-analysis platform: no pandas, no
  SQL, no external data source. Averages, percentage change, and
  differences are all just arithmetic expressions; "which is higher"
  comparisons the agent reasons about directly over numbers it already has
  once "which is higher" no longer requires a tool call.

No new tools were created beyond `calculate`. No shared `common/` module -
this stage's plumbing is fully self-contained, same as every other stage.
"""

import ast
import operator

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()


# ---------------------------------------------------------------------------
# calculate - a safe arithmetic-expression evaluator (no eval())
# ---------------------------------------------------------------------------

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _ALLOWED_BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


@tool
def calculate(expression: str) -> str:
    """Evaluate a numeric arithmetic expression and return the result.

    Supports +, -, *, /, // (floor division), % (modulo), ** (power), and
    parentheses. Use this for anything requiring exact arithmetic - sums,
    averages (e.g. "(12 + 18 + 30) / 3"), percentage change
    (e.g. "((120 - 100) / 100) * 100"), or differences between values -
    instead of computing it yourself, since arithmetic done by an LLM is
    unreliable.
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree)
    except Exception as exc:
        return f"Could not evaluate '{expression}': {exc}"
    return str(result)


tools = [calculate]

SYSTEM_PROMPT = (
    "You are an Analysis Agent, a specialist in calculations, comparisons, "
    "and reasoning over numeric or structured data. You work only with "
    "numbers and data given to you in the conversation - you cannot search "
    "the web or read documents. You have one tool: calculate, which "
    "evaluates arithmetic expressions exactly. Use it for any sum, average, "
    "percentage change, or difference instead of computing by hand, since "
    "you are prone to arithmetic mistakes. For comparisons like 'which is "
    "highest', reason directly over the numbers once you have them. Stay "
    "focused on analysis - you're not a general-purpose assistant."
)

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
    print("Stage 15 analysis agent. Type 'exit' to quit.\n")

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
