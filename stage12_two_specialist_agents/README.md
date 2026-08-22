# Stage 12: Two Specialist Agents

## What was added

Two independent specialist agents, built by repeating Stage 11's pattern
twice with different tools and identities:

- **Research Agent** - copied verbatim from Stage 11. One tool
  (`DuckDuckGoSearchRun`, web search), one identity ("Research Agent").
- **Knowledge Agent** - built the same way, using Stage 3's local
  retrieval tool (`search_knowledge_base` over `knowledge_base/*.md`)
  instead of web search, with its own identity ("Knowledge Agent").

Each agent is a complete, separate graph: its own LLM instance, its own
tool list, its own `SystemMessage`, its own `MemorySaver`, its own
compiled `StateGraph`. Neither graph references the other. `main()` runs
one REPL that talks to both, choosing which agent to call with a plain
string-prefix check (`research:` / `knowledge:`) - not routing logic, not
an LLM decision, just an `if` before either graph is invoked.

No new tools were written. No supervisor, router, or critic was added -
the two agents cannot hand work to each other, share state, or see each
other's tools. That kind of coordination is left for a future stage.

## How do the two agents differ?

| | Research Agent | Knowledge Agent |
|---|---|---|
| Tool | `DuckDuckGoSearchRun` (live web search) | `search_knowledge_base` (local vector-store retrieval) |
| Data source | The open internet, current at query time | `knowledge_base/*.md` in this folder only (solar, wind, hydro) |
| System prompt | "You are a Research Agent... web research..." | "You are a Knowledge Agent... local knowledge base... cannot browse the web..." |
| Can answer | Anything searchable on the web | Only what's in the local markdown files |
| Thread / memory | `thread_id: "research-1"` | `thread_id: "knowledge-1"` (separate `MemorySaver`, separate history) |

Structurally they are identical - both are Stage 2's `agent -> tools ->
agent` loop (`bind_tools`, `ToolNode`, `tools_condition`). What makes them
different specialists is exactly what made Stage 11 a specialist: which
single tool is bound, and what the `SystemMessage` tells the LLM its job
is. Stage 12 just proves that pattern isn't a one-off - you can stamp it
out again with a different tool and get a genuinely different specialist,
with zero new LangGraph mechanism.

## How do they work independently?

There is no shared graph, no shared state, and no communication path
between the two agents:

- Each has its own `ChatOpenAI` instance and its own `bind_tools` call, so
  the Research Agent's LLM never even sees `search_knowledge_base` exists,
  and the Knowledge Agent's LLM never sees `search_web`.
- Each has its own `MemorySaver`, keyed by a different `thread_id`, so
  their conversation histories never mix.
- `main()` decides which graph to `.invoke()` *before* either agent runs,
  based on the prefix you typed. Once a graph is invoked, that agent runs
  its own `agent -> tools -> agent` loop exactly as it would if it were
  the only agent in the file - it has no awareness a second agent exists.

This is what "independent" means at this stage: two specialists that
happen to live in the same process and REPL, not two specialists that
collaborate.

## Architecture

```
                 (prefix check in main(), not agent logic)
                   research: ...        knowledge: ...
                        |                     |
                        v                     v
   START -> agent -> [tool call?] -> tools -> agent -> ... -> END
          (Research    \-> [no tool call] -> END        (Research graph)
           identity +
           search_web)

   START -> agent -> [tool call?] -> tools -> agent -> ... -> END
          (Knowledge    \-> [no tool call] -> END       (Knowledge graph)
           identity +
           search_knowledge_base)
```

Two separate, same-shaped graphs. Nothing connects them.

## How to run

```
.venv\Scripts\activate
python stage12_two_specialist_agents/main.py
```

```
Stage 12: two independent specialist agents.
Type 'research: <question>' or 'knowledge: <question>'.
Type 'exit' to quit.

You: research: who won the most recent Super Bowl?
[Research Agent]: ...(answers using a web search)

You: knowledge: how does solar power work?
[Knowledge Agent]: ...(answers from knowledge_base/solar.md)

You: knowledge: who won the most recent Super Bowl?
[Knowledge Agent]: I don't have information about that in the knowledge
base - it only covers solar, wind, and hydro power.
```

Test:

```
python stage12_two_specialist_agents/test_two_specialist_agents.py
```

This asks the Research Agent a web question and the Knowledge Agent a KB
question, and asserts each one calls only its own tool and never the
other agent's tool.

## What changed compared with Stage 11

Stage 11 established the specialist pattern (one tool + one identity
layered on Stage 2's loop) with a single agent. Stage 12 doesn't add a
new mechanism - it repeats that exact pattern a second time with a
different tool (`search_knowledge_base` instead of `search_web`) and a
different identity, and runs both side by side from one REPL. The new
thing to notice isn't in either agent's code, it's that they can coexist
in the same file/process with zero shared state and zero interaction -
which is the prerequisite for a future stage to add a supervisor that
routes between them.
