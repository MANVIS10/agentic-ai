# Stage 17: Final Multi-Agent Research Assistant

## What was added

The final combined system: Stage 7/8's **planner + human approval** loop,
now wrapped around Stage 16's **supervisor + three specialists + critic**
graph instead of a plain LLM call (Stage 6/7) or a single flat 4-tool agent
(Stage 8).

```
Question -> Plan -> Human approval -> Research each subtask -> Synthesize -> Final answer
                                              |
                                    (per subtask, runs the
                                     full supervisor+critic
                                     pipeline below)
```

Nothing new was invented. This stage is pure **composition** of two graphs
that already existed independently:

- **Outer graph** (`PlannerState`) — `plan`, `human_approval`,
  `research_subtask`, `has_more_subtasks`, `synthesize`, `run_until_settled`
  — copied verbatim from Stage 7/8.
- **Inner graph** (`CriticState`, a `MessagesState`) — the three specialist
  subgraphs (Research/Knowledge/Analysis), `supervisor_node`, `critic_node`,
  both conditional-edge dispatch dicts — copied verbatim from Stage 16.

The only new code is `research_subtask`'s body: instead of Stage 6's bare
`llm.invoke(subtask)` or Stage 8's single 4-tool agent, it now calls
`supervisor_critic_graph.invoke({"messages": [...]})` for **every**
subtask. This proves the insight Stage 13 first demonstrated — a compiled
`StateGraph` invoked inside a node is indistinguishable from any other
function call — generalizes to *any* compiled graph, not just a flat
tool-calling agent.

## Architecture

```
                                   OUTER GRAPH (PlannerState)

START -> plan -> human_approval --(rejected)--> END
                       |
                   (approved)
                       v
              research_subtask  <---- one call per subtask to the INNER
                       |               graph below, fresh each time
              has_more_subtasks
               /            \
          (more)           (done)
            |                 v
            |            synthesize -> END
            ^
            |
    loops back to research_subtask
```

```
                          INNER GRAPH (CriticState / MessagesState)
                          supervisor_critic_graph, invoked once per subtask

START -> supervisor --(route: research/knowledge/analysis)--> one specialist
                                                                     |
                                          research_agent / knowledge_agent / analysis_agent
                                          (each its own agent->tools->agent loop)
                                                                     |
                                                                  critic
                                                          pass -> END
                                                          retry (bounded by MAX_RETRIES,
                                                                 resets to 0 per invocation)
                                                                 -> back to the SAME specialist
```

`research_subtask` only ever sees the inner graph's *return value*
(`result["next"]`, `result["verdict"]`, `result["retry_count"]`,
`result["messages"][-1].content`) — it has no idea a routing decision or a
retry loop happened inside that call, the same way Stage 8's
`research_subtask` had no idea its 4-tool agent's `tools_condition` loop
was running.

## Why two different state schemas don't conflict

`PlannerState` (outer) is a plain-value `TypedDict` — `question`,
`subtasks`, `current_index`, `results`, `final_answer`, `approved`.
`CriticState` (inner) is a `MessagesState` subclass — `messages`, `next`,
`verdict`, `feedback`, `retry_count`. These never merge into one schema.
`research_subtask` is the *only* place they touch, and only through a plain
function call and its return dict — exactly how Stage 8's
`research_agent.invoke(...)` already worked. No shared keys, no special
LangGraph subgraph API needed.

## Design decisions

- **No checkpointer on the inner graph.** `supervisor_critic_graph` is
  compiled without a checkpointer — it's a one-shot helper invoked
  synchronously once per subtask (like Stage 8's `research_agent`), not its
  own multi-turn REPL the way Stage 16 runs it. Only the **outer** planner
  graph is compiled with `checkpointer=MemorySaver()`, since it's the one
  with an `interrupt()`.
- **No per-subtask try/except.** Matches Stage 8 (which has none around its
  inner-graph call) rather than inventing new node-level error handling.
  Error handling lives at the same altitude Stage 16 already puts it:
  wrapped around the whole REPL turn in `main()`, so a transient tool
  failure (e.g. a web search network error) prints `[Error] ...` and lets
  the user try again instead of killing the process.
- **Human approval is always-on**, matching Stage 7/8 exactly — no
  flag/toggle was added to skip it.
- **`results` stays `list[str]`** (answers only, unchanged from Stage
  6/7/8) — no state field was added to record which specialist or verdict
  handled each subtask. That's visible in the console output instead
  (`[Supervisor routed to: ...]` / `[Critic verdict: ...]`), the same way
  Stage 16 already surfaces it, without growing the state schema.
- **Each subtask gets a fresh inner-graph invocation** — no shared
  thread/state across subtasks, so `retry_count` always starts at 0 per
  subtask and retries never leak between them.

## How to run

```
.venv\Scripts\activate
python stage17_final_multi_agent_system/main.py
```

```
Stage 17: final multi-agent research assistant. Type 'exit' to quit.

Research question: How does solar power work, and what is the average of 10, 20, and 30?

Plan (3 subtasks):
  1. Explain the basic principles of solar power generation...
  2. Calculate the average of the three numbers: 10, 20, and 30.
  3. Investigate the efficiency of different solar technology types.
Approve this plan? (y/n): y

Researching: Explain the basic principles of solar power generation...
  [Supervisor routed to: knowledge]
  [Critic verdict: pass, retries used: 0]

Researching: Calculate the average of the three numbers: 10, 20, and 30.
  [Supervisor routed to: analysis]
  [Critic verdict: pass, retries used: 0]

Researching: Investigate the efficiency of different solar technology types.
  [Supervisor routed to: knowledge]
  [Critic verdict: pass, retries used: 0]

Final answer: ...(synthesized answer covering both the solar-power explanation and the calculated average)
```

Test:

```
python stage17_final_multi_agent_system/test_final_multi_agent_system.py
```

This confirms: the inner supervisor+critic pipeline still routes correctly
and stays within `MAX_RETRIES` for one question per specialist (same checks
as Stage 16's own test, run against the embedded copy); the Analysis
Agent's `calculate` tool is actually invoked; the retry cap is enforced in
code even when called directly with `retry_count` already maxed out; and
the outer planner's approve/reject loop produces a `final_answer` when
approved (`Command(resume="y")`) and produces none when rejected
(`Command(resume="n")`).

## What changed compared with Stage 8 and Stage 16

| | Stage 8 | Stage 16 | Stage 17 |
|---|---|---|---|
| Outer planner + approval | Stage 7, unchanged | — (not present) | Stage 7/8, unchanged |
| Per-subtask researcher | one flat 4-tool agent | — (no subtasks; whole question routed once) | full supervisor+critic pipeline, invoked once per subtask |
| Supervisor / routing | — (no routing) | routes the whole question once | routes each *subtask* once |
| Critic / retry | — (no critic) | bounded retry per question | bounded retry per subtask |
| New code | 0 lines beyond composition | 0 lines beyond a 3rd specialist | 0 lines beyond `research_subtask`'s new body |

Stage 8 proved "planner wraps an arbitrary compiled graph per subtask"
using a flat tool agent. Stage 17 proves the same composition works with a
*much* more elaborate compiled graph — supervisor, three specialists, and a
critic with its own retry loop — dropped into that exact same slot with no
other changes required anywhere.
