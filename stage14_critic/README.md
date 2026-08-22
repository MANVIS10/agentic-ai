# Stage 14: Critic / Reviewer

## What was added

A **critic node** that reviews a specialist's answer before it becomes the
final answer - replacing Stage 13's "whatever the specialist says is final"
with a pass/retry check.

The supervisor and both specialists are unchanged from Stage 13:

- **Supervisor** - routes the question to `research` or `knowledge`, once,
  using structured output.
- **Research Agent** - one tool (`DuckDuckGoSearchRun`, web search).
- **Knowledge Agent** - one tool (`search_knowledge_base`, local retrieval
  over `knowledge_base/*.md` - solar, wind, hydro).

No new tools were written. The only new pieces are the critic node, three
new state fields (`verdict`, `feedback`, `retry_count`), the conditional
edge after critic, and one small addition to each specialist node that
attaches the critic's feedback on a retry.

## What is a critic?

A critic is a node whose only job is to judge someone *else's* output - it
doesn't answer the question itself, and it doesn't decide who should answer
it (that's the supervisor's job). It reads the original question and the
specialist's answer and returns one of two verdicts: `pass` (good enough,
this is the final answer) or `retry` (not good enough, send it back with a
note about what's wrong).

## Why retry the same specialist, not re-route through the supervisor?

The supervisor's job was picking *which* specialist is right for the
question - that hasn't changed just because the first attempt was weak. If
the Research Agent gave an inadequate answer to a web-search question, the
fix is a better attempt from the Research Agent, not a second guess about
whether it should have gone to the Knowledge Agent instead. So the critic
reads `state["next"]` - the routing decision the supervisor already made -
and sends a retry straight back to that same specialist node, skipping the
supervisor entirely.

## How is a retry different from just asking again?

If the specialist saw the exact same input twice, a retry wouldn't reliably
change anything. Instead, `research_node`/`knowledge_node` check
`state["feedback"]` and, when it's set, append it as an extra message before
re-invoking the specialist:

```python
if state.get("feedback"):
    messages = messages + [HumanMessage(
        content=f"Reviewer feedback: {state['feedback']} "
        "Please address this and try again."
    )]
```

Nothing extra needs to be tracked to make this work - the specialist's first
answer is already sitting in `state["messages"]` (added there when
`research_node`/`knowledge_node` returned it the first time), so the second
attempt sees its own previous answer plus the reviewer's note, the same way
a person would re-read their own draft next to an editor's comment.

## How is the retry loop kept from running forever?

A module constant, `MAX_RETRIES = 1`, caps it at one retry (two specialist
attempts total). The cap is enforced inside `critic_node` itself, not in the
routing function:

```python
if review["verdict"] == "retry" and state["retry_count"] < MAX_RETRIES:
    return {"verdict": "retry", "feedback": review["feedback"],
            "retry_count": state["retry_count"] + 1}
return {"verdict": "pass", "feedback": ""}
```

Once `retry_count` reaches `MAX_RETRIES`, `critic_node` returns `"pass"`
regardless of what the critic LLM actually thinks - so the conditional edge
after critic only ever has to check one thing (`state["verdict"]`), and the
graph can never loop indefinitely no matter how the LLM judges the answer.
This is the same shape as Stage 6's `current_index < len(subtasks)` loop
guard, just checking a retry count against a cap instead of a list length.

## Architecture

```
                                User
                                 |
                                 v
                          +-------------+
                          | supervisor  |   <- LLM + structured output
                          | (no tools)  |      decides "research" or
                          +-------------+      "knowledge", resets
                                 |              retry_count to 0
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
               \__________________  __________________ __/
                                  \/
                              +--------+
                              | critic |   <- LLM + structured output
                              +--------+      decides "pass" or "retry"
                              /        \
                       verdict=pass     verdict=retry
                      (or retries          (retries remain)
                       exhausted)               |
                          |                     v
                          v          back to the SAME specialist
                        END          node (state["next"]), this time
                     (final answer)  with state["feedback"] attached
```

## Stage 13 vs Stage 14

| | Stage 13 | Stage 14 |
|---|---|---|
| What happens after a specialist answers | Graph ends immediately - that answer is final | A critic reviews it first; only a `pass` verdict ends the graph |
| New LangGraph concept | structured LLM output + conditional edge on a routing field | a second structured-output node judging quality, plus a *bounded retry loop* (conditional edge that can route back to an earlier node) |
| Retry target | n/a | the same specialist node the supervisor originally picked - never back through the supervisor |
| State | `messages`, `next` | adds `verdict`, `feedback`, `retry_count` |
| Loop safety | n/a | `MAX_RETRIES = 1`, enforced inside `critic_node` so the conditional edge stays simple |

The specialists' own internal logic (`agent -> tools -> agent`) and the
supervisor's routing logic are both identical to Stage 13 - Stage 14 only
adds a quality gate after them.

## How to run

```
.venv\Scripts\activate
python stage14_critic/main.py
```

```
Stage 14: supervisor routes, critic reviews before the answer is final.
Just ask - no prefix needed. Type 'exit' to quit.

You: What's the latest SpaceX launch news?
[Supervisor routed to: research]
[Critic: pass]
[Research Agent]: ...(answers using a web search)

You: How does solar power work?
[Supervisor routed to: knowledge]
[Critic: pass]
[Knowledge Agent]: ...(answers from knowledge_base/solar.md)
```

If the critic asks for a retry, you'll also see a line like
`[Critic asked for 1 retry(ies) before passing]` before the final answer.

Test:

```
python stage14_critic/test_critic.py
```

This runs one current-events question and one knowledge-base question
end-to-end (asserting a non-empty final answer, a `retry_count` within
`MAX_RETRIES`, and a final `verdict` of `"pass"`), then calls `critic_node`
directly with `retry_count` already at `MAX_RETRIES` to confirm the retry
cap is actually enforced in code rather than left to the LLM's judgment.

## What changed compared with Stage 13

Stage 13 added the supervisor's routing decision; Stage 14 adds a second
decision-maker after the specialist runs, this time judging the *quality*
of the output rather than *who* should produce it. Nothing about the
supervisor or either specialist's internal loop changed - the new work is
entirely the critic node, the retry-triggering state fields, and the
conditional edge that can send execution backward to an earlier node
instead of only ever moving forward toward `END`.
