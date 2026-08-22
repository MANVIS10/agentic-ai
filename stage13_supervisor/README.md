# Stage 13: Supervisor Agent

## What was added

A **supervisor node** that reads the user's question and decides which of
two specialist agents should answer it - replacing Stage 12's hard-coded
`research:` / `knowledge:` prefix with an actual routing decision made by
an LLM.

The two specialists themselves are unchanged from Stage 12:

- **Research Agent** - one tool (`DuckDuckGoSearchRun`, web search).
- **Knowledge Agent** - one tool (`search_knowledge_base`, local retrieval
  over `knowledge_base/*.md` - solar, wind, hydro).

No new tools were written. Both specialists keep Stage 2's
`agent -> tools -> agent` loop exactly as before; the only new code is the
supervisor node, a shared `next` field in state, and a conditional edge
that reads it.

## What is a supervisor?

A supervisor is a node whose only job is to look at the conversation and
decide **who should handle it next** - it doesn't answer the question
itself. In a multi-agent system it sits in front of the specialists and
picks one, the way a human dispatcher reads an incoming ticket and routes
it to the right team instead of solving it personally.

## Why do we need one?

Stage 12 had two complete, working specialist agents, but no way to choose
between them except a human typing a prefix by hand. That doesn't scale -
a real user won't type `research:` or `knowledge:` before every question,
and a fixed prefix can't understand what the question is actually about.
The supervisor makes that decision automatically by reading the question
itself.

## How does the routing decision work?

The supervisor is a plain LLM call - **no tools bound to it** - with a
system prompt describing the two specialists and what each one is good
for (current events / live web info -> Research Agent; solar, wind, hydro
topics from the local knowledge base -> Knowledge Agent). It reads the
latest message and returns a decision. That decision is stored in the
graph's shared state as `state["next"]`, and a conditional edge sends
execution to `research_agent` or `knowledge_agent` based on that value.

## What does "structured output" mean?

Instead of asking the LLM to write a sentence like *"I think this should
go to the research agent"* and then parsing that text with string checks
or regex, we give the LLM a small typed schema and ask it to fill that in
directly:

```python
class Route(TypedDict):
    next: Literal["research", "knowledge"]

supervisor_llm = ChatOpenAI(model="gpt-4o-mini").with_structured_output(Route)
```

`with_structured_output` constrains the model's reply to match `Route`, so
the response comes back already as `{"next": "research"}` or
`{"next": "knowledge"}` - never as free text, and never as anything other
than one of those two literal values. Nothing to parse, nothing that can
come back malformed.

## How do conditional edges work here?

A conditional edge is a normal LangGraph edge whose destination is decided
at runtime by a function, instead of being fixed at graph-build time:

```python
def route_from_supervisor(state: SupervisorState) -> str:
    return state["next"]

supervisor_graph_builder.add_conditional_edges(
    "supervisor",
    route_from_supervisor,
    {"research": "research_agent", "knowledge": "knowledge_agent"},
)
```

After the `supervisor` node runs and sets `state["next"]`, LangGraph calls
`route_from_supervisor(state)` and sends execution to whichever node the
returned string maps to in the dict. This is the same mechanism Stage 2
already used for `tools_condition` (tool call -> `tools`, no tool call ->
`END`) - here the condition is the supervisor's own routing decision
instead of "did the LLM call a tool."

## Architecture

```
                                User
                                 |
                                 v
                          +-------------+
                          | supervisor  |   <- LLM + structured output
                          | (no tools)  |      decides "research" or
                          +-------------+      "knowledge"
                                 |
                    state["next"] == ?
                    /                        \
        "research"                             "knowledge"
                /                                        \
               v                                          v
      +------------------+                      +-------------------+
      |  research_agent  |                      |  knowledge_agent  |
      | agent -> tools ->|                      | agent -> tools -> |
      |   agent (loop)   |                      |   agent (loop)    |
      +------------------+                      +-------------------+
               |                                          |
        DuckDuckGoSearchRun                    search_knowledge_base
          (web search)                            (local RAG, .md files)
               |                                          |
               \__________________  __________________ __/
                                  \/
                                Answer
                        (the specialist's own
                         final message, returned
                         as the graph's output)
```

`supervisor` is the only node with a routing choice; `research_agent` and
`knowledge_agent` both always end the graph (`add_edge(..., END)`) - the
specialist's own final message is returned directly as the answer, with
no critic, retry, or second opinion layered on top.

## Stage 12 vs Stage 13

| | Stage 12 | Stage 13 |
|---|---|---|
| How the specialist is picked | Human types `research:` / `knowledge:` prefix | Supervisor LLM classifies the question |
| Where the decision lives | A string check in `main()`, before either graph runs | `state["next"]`, set by a graph node, read by a conditional edge |
| Graphs involved | Two separate, unconnected graphs | One graph: supervisor + both specialists as nodes |
| New LangGraph concept | none (repeats Stage 11's pattern twice) | structured LLM output + conditional edge on a routing field |
| Memory | Two separate `MemorySaver`s, one per agent, keyed by different `thread_id`s | One `MemorySaver` on the outer supervisor graph; the specialist subgraphs are stateless (they receive the full message history from the outer state each call) |

The specialists' own internal logic (`agent -> tools -> agent`) is
identical in both stages - Stage 13 only adds a decision-maker in front of
them.

## How to run

```
.venv\Scripts\activate
python stage13_supervisor/main.py
```

```
Stage 13: supervisor routes your question to a specialist.
Just ask - no prefix needed. Type 'exit' to quit.

You: What's the latest SpaceX launch news?
[Supervisor routed to: research]
[Research Agent]: ...(answers using a web search)

You: How does solar power work?
[Supervisor routed to: knowledge]
[Knowledge Agent]: ...(answers from knowledge_base/solar.md)
```

Test:

```
python stage13_supervisor/test_supervisor.py
```

This asks one current-events question and one knowledge-base question,
and asserts `state["next"]` (the supervisor's actual routing decision,
not a guess based on the answer text) matches `"research"` and
`"knowledge"` respectively, and that the routed specialist produced a
non-empty answer.
