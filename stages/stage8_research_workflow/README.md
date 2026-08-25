# Stage 8 — Better Research Workflow

## What was added

Stage 7's planner (plan -> human approval -> research loop -> synthesize)
stays exactly the same. The only change is *how* each subtask gets
researched: instead of `research_subtask` calling `llm.invoke(subtask)`
directly, it now hands the subtask to a small tool-calling agent that has
all four tools from Stages 2-5 bound at once — `DuckDuckGoSearchRun`
(web search), `search_knowledge_base` (local docs), `fetch_webpage`, and
`fetch_pdf`. The LLM picks whichever tool actually fits each subtask,
instead of answering from training data alone.

No new tools were created. No memory, multi-agent, supervisor, or critic
was added — just the existing capabilities combined behind the existing
planner.

## Concept demonstrated

- **Composition, not a new LangGraph primitive.** The inner research agent
  is the exact same `agent -> tools -> agent` graph shape Stages 2-5 each
  built on their own (`bind_tools`, `ToolNode`, `tools_condition`) - just
  with four tools bound together instead of one. It's compiled on its own
  and invoked like a plain function (`research_agent.invoke(...)`) from
  inside the outer planner's `research_subtask` node.
- This shows a compiled graph is just a callable, so an outer graph's node
  can wrap an inner graph the same way it could wrap any other function
  call - no supervisor or agent-of-agents machinery needed for that.

## Architecture

```
START -> plan -> human_approval --(approved)--> research_subtask -> ... -> synthesize -> END
                      \--(rejected)---------------------------------------------------> END

research_subtask, per subtask:
    subtask -> [ inner agent: agent -> tools? -> agent ] -> answer
```

The outer graph (`plan`, `human_approval`, `route_after_approval`,
`research_subtask`, `has_more_subtasks`, `synthesize`) is Stage 7,
unchanged. The inner `research_agent` graph is new: two nodes (`agent`,
`tools`), one conditional edge (`tools_condition`), no checkpointer - it's
invoked fresh for each subtask rather than carrying conversation state.

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage8_research_workflow/main.py
```

Ask a research question, read the printed plan, approve it with `y` at the
prompt, and watch each subtask get researched with whichever tool the
model picks (check the terminal - the ReAct loop still runs per subtask,
it just isn't printed step-by-step). Type `exit` or `quit` to leave.

## Test

```
python stage8_research_workflow/test_research_workflow.py
```

Runs the full graph on a fixed question, auto-approves the plan with
`Command(resume="y")` instead of the interactive prompt, and asserts a
non-empty `final_answer` comes out with one result per subtask.

## What changed vs. Stage 7

- Added `search_web`, `search_knowledge_base`, `fetch_webpage`, and
  `fetch_pdf` - duplicated verbatim from Stages 2-5, per the project's
  no-shared-`common/`-module rule - plus a copy of Stage 3's
  `knowledge_base/` markdown files.
- Added the small `research_agent` subgraph (`agent`, `tools` nodes) that
  binds all four tools together.
- `research_subtask` now calls `research_agent.invoke(...)` instead of
  `llm.invoke(subtask)`.
- `PlannerState`, `plan`, `human_approval`, `route_after_approval`,
  `has_more_subtasks`, `synthesize`, `run_until_settled`, and `main()` are
  all unchanged from Stage 7.
