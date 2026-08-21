# Stage 6 — Planner

## What was added

A planner that takes one research question, breaks it into 2-3 subtasks,
answers each subtask one at a time, then combines the answers into a
single final response. No tool is bound in this stage - the LLM is used
directly at each step (breaking down the question, answering a subtask,
combining results).

Note: like `stage4_web_fetch` and `stage5_pdf_fetch`, this isn't the
folder name the original top-level roadmap used for this concept (it was
called `stage4_planner`) - it's numbered `stage6_planner` to match where it
actually landed in the build order, since Stages 4 and 5 were already
taken by the web-fetch and PDF-fetch tool stages.

## Concept demonstrated

- **A custom state schema, not `MessagesState`.** Stages 1-5 all
  accumulated a chat message list. This stage has no chat history to
  keep - it tracks a plan instead: the original question, the subtask
  list, how far through the list we are, the results gathered so far, and
  the final answer.
- **A hand-written conditional loop, not `tools_condition`.** Every prior
  stage's loop was `tools_condition` deciding "did the model call a tool?"
  Here the loop condition is one we write ourselves: "is there another
  subtask left to research?" That function (`has_more_subtasks`) either
  routes back to `research_subtask` (loop) or forward to `synthesize`
  (done) - this is what a conditional edge looks like when it isn't the
  built-in ReAct pattern.
- **Plan -> execute -> combine.** `plan` calls the LLM once to produce a
  short subtask list. `research_subtask` calls the LLM once per subtask,
  looping via the conditional edge above until every subtask has an
  answer. `synthesize` calls the LLM one last time to merge everything
  into a final answer grounded in the original question.

## Architecture

```
START -> plan -> research_subtask --(more subtasks?)--> research_subtask
                        |
                        v (no more subtasks)
                    synthesize -> END
```

Three nodes, all plain `gpt-4o-mini` calls (no tools bound):
- `plan` - question -> list of 2-3 subtasks
- `research_subtask` - one subtask -> one answer, appended to `results`,
  runs again for each remaining subtask (loop)
- `synthesize` - question + all results -> final answer

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage6_planner/main.py
```

Give it a research question (e.g. "What causes rainbows and why are they
curved?") and watch it print the generated subtasks, then each subtask
being researched in turn, then the combined final answer. Type `exit` or
`quit` to leave.

## What changed vs. Stage 5

- No tool bound to the LLM at all - Stages 2-5 each bound exactly one
  tool (`DuckDuckGoSearchRun`, `search_knowledge_base`, `fetch_webpage`,
  `fetch_pdf`); this stage uses the LLM directly for planning, research,
  and synthesis.
- New state schema (`PlannerState`, a `TypedDict`) replaces `MessagesState`
  - there's no growing chat message list to manage.
  - `ToolNode` / `tools_condition` are gone; replaced by three plain
  function nodes and one hand-written conditional-edge function
  (`has_more_subtasks`).
- No `MemorySaver` / `thread_id` - each invocation processes one research
  question end-to-end rather than a multi-turn conversation, so there's no
  conversation history to check-point.
- Stages 1-5 are untouched.
