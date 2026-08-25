# Stage 9: Simple Memory

## What was added

A second, separate kind of memory sitting next to Stage 1's chatbot:
long-term memory, implemented as a single fact saved to a JSON file on
disk (`long_term_memory.json`) via two plain functions, `save_memory` and
`load_memory`.

Two typed commands trigger it, handled as plain string checks (no LLM
tool-calling):

- `remember: <text>` - saves `<text>` to disk, no LLM call for that turn.
- `recall` - reads the fact back from disk, no LLM call for that turn.

Anything else is an ordinary chat turn, identical to Stage 1.

## Concept demonstrated

The difference between **LangGraph state** and **long-term memory**:

- State (`MessagesState` + `MemorySaver`) is scoped to one `thread_id` and
  lives only as long as the checkpointer does. It's what lets the bot
  remember what you said two turns ago *in the same conversation*.
- Long-term memory here is just a file. It has no `thread_id`, no
  checkpointer, and no graph involvement at all - it survives switching
  to a different `thread_id`, and it survives the whole process exiting
  and being started again.

Concretely: `remember: my favorite color is teal` while chatting on
`thread_id="1"`, then restart `main.py` (a brand new process, brand new
checkpoint) and type `recall` - the fact is still there, because it was
never part of the graph's state to begin with.

## Architecture

Same one-node graph as Stage 1:

```
START -> chatbot -> END
```

`chatbot` is unchanged from Stage 1. `save_memory`/`load_memory` sit
outside the graph entirely - they're called directly from the terminal
loop before the graph is ever invoked, not as tools the LLM decides to
call.

## How to run

```
.venv\Scripts\activate
python stage9_simple_memory/main.py
```

Try:

```
You: remember: my favorite color is teal
Bot: Got it, I'll remember: my favorite color is teal

You: recall
Bot: You told me to remember: my favorite color is teal
```

Then quit, restart the script, and type `recall` again - it still
remembers, even though it's a brand new conversation with no chat
history.

Test:

```
python stage9_simple_memory/test_simple_memory.py
```

## What changed compared with Stage 8

Stage 8 was about composing a compiled subgraph into a bigger research
workflow. Stage 9 is unrelated to that - it goes back to Stage 1's
minimal one-node chatbot and adds only long-term memory alongside it, to
isolate the state-vs-memory concept without any tool-calling or planning
machinery in the way.
