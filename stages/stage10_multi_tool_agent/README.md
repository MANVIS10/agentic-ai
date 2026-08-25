# Stage 10: Multi-Tool Agent

## What was added

A single chat agent with all four tools from Stages 2-5 bound at once:

- `search_web` (`DuckDuckGoSearchRun`, Stage 2)
- `search_knowledge_base` (local markdown retrieval, Stage 3)
- `fetch_webpage` (HTTP fetch + HTML parsing, Stage 4)
- `fetch_pdf` (PDF download + text extraction, Stage 5)

No new tools were written - all four are duplicated verbatim from their
original stages, plus the `knowledge_base/` markdown docs Stage 3 needs.

## Concept demonstrated

**Tool selection**, isolated from everything else. Given a question, the
LLM decides on its own which one of four unrelated tools (if any) actually
fits, then calls it and uses the result to answer - no planner breaking
the question into subtasks first, no human approval step, no supervising
agent choosing on the LLM's behalf.

This is the plain version of what Stage 8 buried one level down: Stage 8
bound the same four tools together too, but only as a node *inside* a
bigger plan -> approve -> research -> synthesize graph. Here the four-tool
agent isn't a component of something bigger - it IS the whole graph.

## Architecture

Exactly Stage 2's shape, with four tools bound instead of one:

```
START -> agent -> [tool call?] -> tools -> agent -> ... -> END
                 \-> [no tool call] -> END
```

- `agent`: calls the LLM (with all four tools bound via `bind_tools`).
- `tools`: a prebuilt `ToolNode` that executes whichever tool the LLM
  asked for.
- `tools_condition`: routes to `tools` if the LLM's response contains a
  tool call, otherwise straight to `END`.
- `tools -> agent`: after a tool runs, control returns to the LLM so it
  can either call another tool or give a final answer - the ReAct loop.
- `MemorySaver` + `thread_id`: conversation memory across turns, same as
  every earlier stage.

## How to run

```
.venv\Scripts\activate
python stage10_multi_tool_agent/main.py
```

Try asking questions that should each reach for a different tool:

```
You: What is solar power?
Bot: ...(answers using search_knowledge_base)

You: Who is the current US president?
Bot: ...(answers using search_web)

You: What's on this page? https://example.com
Bot: ...(answers using fetch_webpage)

You: What's 2 + 2?
Bot: 4   (no tool needed)
```

Test:

```
python stage10_multi_tool_agent/test_multi_tool_agent.py
```

This asks a knowledge-base question, a web-search question, and a
no-tool-needed question, and asserts the expected tool got called (or
didn't) for each.

## What changed compared with Stage 8

Stage 8 was about *composition* - using a compiled tool-agent graph as one
node inside a bigger planner graph, with human approval gating the whole
plan. Stage 10 strips all of that away: no planner, no subtasks, no
interrupt/approval. It's the smallest possible demonstration of an LLM
choosing among several real tools for a single question, going back to
Stage 2's flat `agent -> tools -> agent` shape rather than Stage 8's
nested one.
