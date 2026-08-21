# Stage 2 — Tool Agent (Web Research via ReAct)

## What was added

The chatbot can now decide, on its own, to search the web when it doesn't
already know the answer, instead of always answering directly from the
model's training data.

## Concept demonstrated

- **Tool binding (`llm.bind_tools`)** — the LLM is given a tool's schema
  (`DuckDuckGoSearchRun`, no API key required) so it can *choose* to call it
  instead of responding in plain text.
- **`ToolNode`** — a prebuilt LangGraph node that executes whichever tool
  the LLM asked for and feeds the result back into the message state.
- **Conditional edges (`tools_condition`)** — the graph now branches instead
  of running straight through: after the chatbot responds, LangGraph checks
  whether that response was a tool call and routes to `tools` if so,
  otherwise to `END`.
- **The ReAct loop** — Reason (LLM decides what to do) -> Act (call the
  tool) -> Observe (tool result goes back into state) -> Reason again ->
  ... -> final answer. This is the `chatbot -> tools -> chatbot -> ...`
  cycle in the graph below.

## Architecture

```
START -> chatbot --(tool call?)--> tools -> chatbot -> ... -> END
              \--(no tool call)-----------------------------> END
```

Two nodes: `chatbot` (calls `gpt-4o-mini` with tools bound) and `tools`
(runs the requested tool, here just web search). `tools_condition` is the
router that decides which edge to take after every chatbot turn.

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage2_tool_agent/main.py
```

Ask something the model wouldn't know from training data alone (e.g. recent
news) to see it trigger a search. Type `exit` or `quit` to leave.

## What changed vs. Stage 1

- Added `DuckDuckGoSearchRun` as a bound tool and a `ToolNode` to execute it.
- Replaced the single straight-line edge (`chatbot -> END`) with a
  conditional edge (`tools_condition`) plus a loop-back edge
  (`tools -> chatbot`), so the graph can branch and iterate instead of
  running exactly once.
- Everything else (LLM setup, `MemorySaver`, per-thread config, REPL loop)
  is unchanged from Stage 1 — duplicated here rather than shared, per the
  project's no-shared-abstraction rule.
