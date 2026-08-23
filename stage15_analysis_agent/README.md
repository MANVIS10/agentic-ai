# Stage 15: Analysis Agent

## What was added

A third specialist agent, alongside Stage 11's Research Agent and Stage
12's Knowledge Agent: an **Analysis Agent** whose job is calculations,
comparisons, and reasoning over numeric or structured data given to it in
the conversation.

Mechanically it's nothing new - the exact same `agent -> tools -> agent`
loop as every tool-using stage since Stage 2 (`bind_tools`, `ToolNode`,
`tools_condition`, `MemorySaver`), narrowed to one tool plus a
`SystemMessage` identity, the same recipe Stage 11 and Stage 12 used. What's
different is the *kind* of tool: Research and Knowledge both retrieve
information from somewhere (the web / local documents). The Analysis
Agent's tool doesn't retrieve anything - it computes.

## What questions does it handle?

Anything that boils down to arithmetic or comparison over numbers already
present in the question:

- "What is the average of 45, 67, 89, and 23?"
- "Revenue went from $2,400 to $3,000. What's the percentage increase?"
- "Books sold 120, Electronics sold 340, Clothing sold 210 - which
  category has the highest value, and by how much?"
- "Compare 15% of 200 versus 30% of 90 - which is bigger?"

It has no access to the web or any document store, so it can't answer
"what were Q3's actual sales" - only reason over numbers it's handed
directly, matching its system prompt ("You work only with numbers and data
given to you in the conversation").

## The `calculate` tool

A single arithmetic-expression evaluator, e.g. `"(12 + 18 + 30) / 3"` or
`"((3000 - 2400) / 2400) * 100"`. It's deliberately the simplest possible
"calculator," not a data-analysis platform:

- No `eval()`. The expression is parsed with Python's `ast` module and
  walked by hand, allowing only numeric literals and the operators `+ - *
  / // % **` (plus unary `+`/`-`). No names, no function calls, no
  attribute access - there's nothing to sandbox-escape.
- No pandas, no SQL, no external data source. Averages, percentage
  change, and differences are all just arithmetic expressions once you
  write them out; "which is highest" comparisons the agent reasons about
  directly over numbers it already has, without needing a tool call.
- Fails gracefully. Confirmed during manual testing: the model once tried
  `calculate("max(120, 340, 210)")`, which the evaluator correctly rejects
  as an unsupported function call. It returned an error string instead of
  crashing the graph, and the agent recovered by computing each number
  individually and comparing them itself - same "tools fail with a
  message, not a crash" principle from Stage 4/5.

## How is it different from the Research Agent and Knowledge Agent?

| | Research Agent (11) | Knowledge Agent (12) | Analysis Agent (15) |
|---|---|---|---|
| Tool | `search_web` (DuckDuckGo) | `search_knowledge_base` (local docs) | `calculate` (arithmetic) |
| Source of truth | the live web | project's markdown files | numbers already in the conversation |
| Kind of tool | retrieval | retrieval | computation |
| Typical question | "who won X" | "what does our doc say about Y" | "what's the average / % change / which is bigger" |

All three share the identical graph shape - only the bound tool and the
`SystemMessage` identity change. That's the point: a specialist is a
narrow toolset plus a declared role, not a new mechanism each time.

## LangGraph flow

```
START -> agent -> [tool call?] -> tools -> agent -> ... -> END
       (+ system      \-> [no tool call] -> END
        prompt)
```

- `agent`: prepends the Analysis Agent `SystemMessage`, then calls the LLM
  (with `calculate` bound via `bind_tools`).
- `tools`: a prebuilt `ToolNode` that runs `calculate` when asked.
- `tools_condition`: routes to `tools` if the LLM's response contains a
  tool call, otherwise straight to `END`.
- `tools -> agent`: after the calculation runs, control returns to the LLM
  so it can use the result in its final answer (or calculate again for a
  multi-step question).
- `MemorySaver` + `thread_id`: conversation memory across turns, same as
  every earlier stage.

## How it will later connect to the Supervisor

This agent is built and tested standalone, exactly like Stage 11 and Stage
12 were before Stage 13 added a supervisor in front of them. No supervisor,
router, or critic exists yet - this stage doesn't add one. When a
supervisor stage is eventually extended to three specialists, the Analysis
Agent's compiled `graph` would be wrapped as a node the same way Stage 13
wrapped the Research and Knowledge subgraphs: the supervisor classifies a
question as research / knowledge / analysis and invokes this graph exactly
like calling any other function - no changes needed here to make that
possible.

## How to run

```
.venv\Scripts\activate
python stage15_analysis_agent/main.py
```

```
You: What is the average of 45, 67, 89, and 23?
Bot: The average of 45, 67, 89, and 23 is 56.0.

You: Revenue went from $2,400 to $3,000. What's the percentage increase?
Bot: The percentage increase in revenue is 25%.
```

Test:

```
python stage15_analysis_agent/test_analysis_agent.py
```

This asks an average, a percentage-increase, and two comparison questions
(one that needs `calculate`, one that doesn't), and asserts the tool got
called only when arithmetic was actually needed and each answer contains
the expected number.

## What changed compared with Stage 12

Stage 12 proved specialization generalizes to a second agent with zero
shared state. Stage 15 proves it a third time with a *different kind* of
specialist - one whose tool computes instead of retrieves - and is the
last of the three specialists named in the project spec (Research,
Knowledge, Analysis) to get built. No supervisor, critic, memory, or
database was added; those remain future stages.
