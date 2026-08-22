"""
Stage 9: same one-node chatbot as Stage 1, plus a second, separate kind of
memory sitting next to it.

New concept vs. Stage 1-8:
- Stage 1's `MemorySaver` gives the graph *state* memory: it remembers the
  conversation, but only for one `thread_id`, and only for as long as the
  process (or checkpointer) is alive. Switch threads, or restart the script,
  and that history is gone.
- This stage adds *long-term* memory: a single fact written to a JSON file
  on disk with `save_memory`, read back with `load_memory`. It has nothing
  to do with the graph, the checkpointer, or `thread_id` - it's just a file.
  That's what makes it "long-term": it survives a different thread_id, and
  it survives the process exiting and being started again.

The two commands below are handled as plain string checks, not an LLM tool
call - there's no decision to make about *which* tool to use, so bind_tools
would only add ceremony here:
- "remember: <text>"  -> saved to disk, LLM is not called for this turn.
- "recall"            -> read from disk, LLM is not called for this turn.
- anything else       -> ordinary LLM chat turn, using Stage 1's thread-
                         scoped conversation state exactly as before.
"""

import json
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

load_dotenv()

llm = ChatOpenAI(model="gpt-4o-mini")

MEMORY_FILE = Path(__file__).parent / "long_term_memory.json"


def save_memory(text: str, path: Path = MEMORY_FILE) -> None:
    path.write_text(json.dumps({"fact": text}), encoding="utf-8")


def load_memory(path: Path = MEMORY_FILE) -> str | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["fact"]


def chatbot(state: MessagesState):
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


graph_builder = StateGraph(MessagesState)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_edge(START, "chatbot")
graph_builder.add_edge("chatbot", END)

graph = graph_builder.compile(checkpointer=MemorySaver())


def main():
    config = {"configurable": {"thread_id": "1"}}
    print("Stage 9 chatbot with long-term memory. Type 'exit' to quit.")
    print("Try: 'remember: <something>', then 'recall'.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break

        if user_input.lower().startswith("remember:"):
            fact = user_input.split(":", 1)[1].strip()
            save_memory(fact)
            print(f"Bot: Got it, I'll remember: {fact}\n")
            continue

        if user_input.lower() == "recall":
            fact = load_memory()
            if fact is None:
                print("Bot: I don't remember anything yet.\n")
            else:
                print(f"Bot: You told me to remember: {fact}\n")
            continue

        result = graph.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=config,
        )
        print(f"Bot: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
