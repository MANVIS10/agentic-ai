# Stage 1 — Stateful Chatbot

## What was added

The first graph in the project: a single-node chatbot that remembers the
conversation across turns, run from a terminal REPL.

## Concept demonstrated

- **`StateGraph`** — the graph object that defines how state (a list of
  messages) flows between nodes.
- **A node** — just a plain function that takes the current state and
  returns a partial update to it.
- **A checkpointer (`MemorySaver`)** — saves state after every step, keyed
  by `thread_id`, so the same conversation can be resumed turn to turn
  without the caller re-sending history.

## Architecture

```
START -> chatbot -> END
```

One node, `chatbot`, calls the LLM (`gpt-4o-mini`) with the current message
list and appends its response. No branching, no tools — just the minimal
loop needed to prove the graph + memory pattern works.

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage1_chatbot/main.py
```

Type messages at the `You:` prompt; type `exit` or `quit` to leave. Memory
is scoped to `thread_id="1"`, so the bot remembers earlier turns in the same
run.

## What changed vs. previous stage

N/A — this is the first stage.
