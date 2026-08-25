# Stage 11: First Specialist Agent

## What was added

A single "Research Agent": Stage 2's tool-calling agent (`agent -> tools ->
agent`, `bind_tools`, `ToolNode`, `tools_condition`, `MemorySaver`), with
one thing added - a `SystemMessage` prepended to every turn that gives the
agent an explicit identity and job:

> You are a Research Agent, a specialist whose only job is web research.
> You have one tool: web search. ...

No new tools were written. `search_web` (`DuckDuckGoSearchRun`) is
duplicated verbatim from Stage 2.

## What is an agent?

An "agent" here means an LLM that can *act*, not just answer: given a
question, it can choose to call a tool, look at the result, and decide
whether it needs to act again or is ready to give a final answer. That
loop - reason, act, observe, repeat - is what Stage 2 introduced as the
ReAct pattern, and every tool-using stage since (3, 4, 5, 8, 10) has reused
the same shape. An agent is really just three ingredients: an LLM, one or
more tools it's allowed to call, and a loop that keeps handing control back
to the LLM until it stops asking for tools.

## What makes this a *specialist* agent?

Nothing new mechanically - Stage 11 is the exact same `bind_tools` +
`ToolNode` + `tools_condition` loop as Stage 2. What makes it a
*specialist* is two choices layered on top of that plain mechanism:

1. **One tool, for one job.** It only has web search - it can't retrieve
   from a knowledge base, fetch a URL, or read a PDF. Its capabilities
   *are* its specialty.
2. **A declared identity.** The `SystemMessage` tells the LLM what it is
   and what it's for, before it ever sees the user's question. Stage 2's
   agent had a tool but no stated purpose; this agent knows it's a
   "Research Agent" and stays inside that role.

A specialist agent, in other words, is a generalist tool-agent with its
scope narrowed on purpose - fewer tools, and a system prompt that commits
it to a specific job. That's exactly the shape a future supervisor would
need to route work to: a named specialist with a known, narrow competency,
rather than one agent that can do a bit of everything.

## How is it different from the Stage 10 multi-tool agent?

Stage 10 and Stage 11 are structurally the same graph (`bind_tools` +
`ToolNode` + `tools_condition`) but represent opposite design choices:

| | Stage 10 | Stage 11 |
|---|---|---|
| Tools bound | 4 (search, KB retrieval, web fetch, PDF fetch) | 1 (search) |
| System prompt / identity | none | "You are a Research Agent..." |
| Design goal | let the LLM pick whichever of several unrelated tools fits a question | commit the agent to one job, done well |
| Analogy | a generalist assistant | a specialist you'd hand a specific kind of task to |

Stage 10 isolates *tool selection* - given many options, which one does the
model choose? Stage 11 isolates *specialization* - what does it mean for
an agent to have a defined role instead of being a jack-of-all-tools? Both
reuse Stage 2's loop unchanged; only what's bound to it (tools + prompt)
differs.

## How does it use its tool?

Exactly like Stage 2: `search_web` is bound to the LLM with `bind_tools`,
so the LLM can emit a tool call instead of answering directly. `ToolNode`
executes whatever call it makes, and `tools_condition` routes back to the
`agent` node afterward so the LLM can read the search result and give a
final answer (or search again). The only difference from Stage 2 is that
the `agent` node now prepends the `SystemMessage` to `state["messages"]`
before calling the LLM, on every turn.

## Architecture

```
START -> agent -> [tool call?] -> tools -> agent -> ... -> END
       (+ system      \-> [no tool call] -> END
        prompt)
```

- `agent`: prepends the `SystemMessage` identity, then calls the LLM (with
  `search_web` bound via `bind_tools`).
- `tools`: a prebuilt `ToolNode` that runs `search_web` when asked.
- `tools_condition`: routes to `tools` if the LLM's response contains a
  tool call, otherwise straight to `END`.
- `tools -> agent`: after the search runs, control returns to the LLM so it
  can answer using the result (or search again) - the ReAct loop.
- `MemorySaver` + `thread_id`: conversation memory across turns, same as
  every earlier stage.

## How to run

```
.venv\Scripts\activate
python stage11_research_agent/main.py
```

```
You: Who won the most recent Super Bowl?
Bot: ...(answers using a web search)

You: What's 2 + 2?
Bot: 4   (no tool needed)
```

Test:

```
python stage11_research_agent/test_research_agent.py
```

This asks a research question and a no-tool question, and asserts the
search tool got called (or didn't) for each.

## What changed compared with Stage 10

Stage 10 bound four unrelated tools with no declared role, so the concept
under test was tool *selection*. Stage 11 strips that back down to one
tool and adds a system prompt naming the agent's job, so the concept under
test is *specialization* - narrowing an agent's scope on purpose rather
than giving it everything and letting it pick. Both stages share Stage 2's
loop unchanged.
