# Stage 16: Three-Specialist Supervisor

## What was added

A third branch through the supervisor+critic graph from Stage 13/14, so the
**Analysis Agent** (built standalone in Stage 15) is now routed to like the
other two specialists instead of only being reachable on its own.

- **Supervisor** - routes the question to `research`, `knowledge`, or
  `analysis`, once, using structured output. Unchanged from Stage 13/14
  except its routing `Literal` widened from two values to three and its
  system prompt gained a third bullet describing the Analysis Agent.
- **Research Agent** - one tool (`DuckDuckGoSearchRun`, web search).
  Unchanged from Stage 11/13/14.
- **Knowledge Agent** - one tool (`search_knowledge_base`, local retrieval
  over `knowledge_base/*.md` - solar, wind, hydro). Unchanged from
  Stage 12/13/14.
- **Analysis Agent** - one tool (`calculate`, a safe `ast`-based arithmetic
  evaluator). Ported from Stage 15 with no logic changes - only its node
  function was renamed (`agent` -> `analysis_agent_node`) to match the
  other two specialists' naming, and its subgraph is compiled without its
  own checkpointer since the outer supervisor graph owns state here.
- **Critic** - reviews whichever specialist answered and can send one
  bounded retry back to that same specialist. **Zero code changes** from
  Stage 14: `critic_node` only ever reads `state["messages"][0]` (the
  original question) and `state["messages"][-1]` (the latest answer) - it
  never looks at which specialist produced the answer, so it needed no
  changes to support a third one.

No new tools, no new planner, no new memory, no databases. The only new
code is the Analysis Agent's subgraph/wrapper node and the extra entry in
each of the two conditional-edge dispatch dicts (supervisor -> specialist,
critic -> specialist-or-end).

## Why did the critic need no changes at all?

This is the main thing this stage demonstrates. Stage 14's critic was
already written generically - "read the question, read the latest answer,
judge it" - with nothing in its logic that names or special-cases a
specialist. Widening the supervisor's routing from two choices to three
only touches the *routing* layer (the `Route`/`CriticState` literal type and
the two dispatch dicts); the *review* layer sitting downstream of routing
didn't need to know that widening happened. That separation - "who answers"
(supervisor + specialists) vs. "was the answer good" (critic) - is exactly
why extending to N specialists was mechanical instead of a redesign.

## Architecture

```
                                User
                                 |
                                 v
                          +-------------+
                          | supervisor  |   <- LLM + structured output
                          | (no tools)  |      decides "research",
                          +-------------+      "knowledge", or "analysis";
                                 |              resets retry_count to 0
                    state["next"] == ?
                 /                 |                 \
        "research"           "knowledge"           "analysis"
              /                    |                     \
             v                     v                       v
   +------------------+  +-------------------+   +-------------------+
   |  research_agent  |  |  knowledge_agent  |   |  analysis_agent   |
   | agent -> tools ->|  | agent -> tools -> |   | agent -> tools -> |
   |   agent (loop)   |  |   agent (loop)    |   |   agent (loop)    |
   +------------------+  +-------------------+   +-------------------+
             \                     |                     /
              \____________________|____________________/
                                   v
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

## Stage 14 vs Stage 16

| | Stage 14 | Stage 16 |
|---|---|---|
| Specialists | Research, Knowledge | Research, Knowledge, Analysis |
| Routing field | `Literal["research", "knowledge"]` | `Literal["research", "knowledge", "analysis"]` |
| Supervisor conditional edge | 2-entry dispatch dict | 3-entry dispatch dict |
| Critic conditional edge | 2 specialist entries + `"end"` | 3 specialist entries + `"end"` |
| Critic node logic | pass/retry, question + latest answer | **unchanged** - same code, same signature |
| New tools | none | none |

## How to run

```
.venv\Scripts\activate
python stage16_three_specialist_supervisor/main.py
```

```
Stage 16: supervisor routes your question to one of three specialists.
Just ask - no prefix needed. Type 'exit' to quit.

You: What's the latest SpaceX launch news?
[Supervisor routed to: research]
[Critic verdict: pass, retries used: 0]
[Research Agent]: ...(answers using a web search)

You: How does solar power work?
[Supervisor routed to: knowledge]
[Critic verdict: pass, retries used: 0]
[Knowledge Agent]: ...(answers from knowledge_base/solar.md)

You: What is the average of 12, 18, and 30?
[Supervisor routed to: analysis]
[Critic verdict: pass, retries used: 0]
[Analysis Agent]: The average of 12, 18, and 30 is 20.0.
```

Test:

```
python stage16_three_specialist_supervisor/test_three_specialist_supervisor.py
```

This runs one question per specialist end-to-end through the full graph
(asserting the supervisor's routing decision, a non-empty final answer, a
`retry_count` within `MAX_RETRIES`, and a final `verdict` of `"pass"`),
separately confirms the Analysis Agent's subgraph actually calls its
`calculate` tool for an arithmetic question (checked against the subgraph
directly, since the outer graph's wrapper nodes fold each specialist's
result down to just its last message and drop intermediate tool-call
messages), and calls `critic_node` directly with `retry_count` already at
`MAX_RETRIES` to confirm the retry cap is enforced in code.

## What changed compared with Stage 14

Stage 14 added the critic on top of Stage 13's two-specialist supervisor.
Stage 16 adds a third specialist to that same supervisor+critic graph -
purely additive: one more specialist subgraph (ported unchanged from
Stage 15), one more entry in each conditional-edge dispatch dict, and a
widened routing type. The supervisor's routing mechanism and the critic's
review logic are both structurally identical to Stage 14.
