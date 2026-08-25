# Stage 7 — Human-in-the-Loop Plan Approval

## What was added

The same planner as Stage 6, except the plan is now shown to a human and
must be approved before any subtask research happens. Reject the plan and
the run stops immediately — no research, no synthesis.

## Concept demonstrated

- **`interrupt()`** — called from inside a node (`human_approval`), it
  pauses the graph mid-run and hands a value back out to the caller instead
  of returning normally. The graph isn't finished — it's parked at that
  node, waiting for an answer.
- **`Command(resume=...)`** — how the caller wakes the graph back up,
  feeding in the human's answer and continuing execution from exactly
  where it paused, in the same run, with the same state.
- **Why a checkpointer is required** — `MemorySaver` (already in use since
  Stage 1) is what lets the graph remember it was paused mid-node for a
  given `thread_id`, so `Command(resume=...)` can pick up in place instead
  of starting the graph over.

## Architecture

```
START -> plan -> human_approval --(approved)--> research_subtask -> ... -> synthesize -> END
                       \--(rejected)---------------------------------------------------> END
```

Four nodes: `plan`, `research_subtask`, and `synthesize` are unchanged from
Stage 6. `human_approval` is new — it calls `interrupt()` with a yes/no
prompt and waits. `plan` already prints the subtask list before
`human_approval` runs, so that print *is* the spec's "show plan" step; the
interrupt itself only needs to ask for a decision, not repeat the plan.

Rejecting doesn't dead-end the graph awkwardly or need a dedicated
"cancelled" node — `route_after_approval` just routes straight to `END`.
`final_answer` is simply never set, and `main()` checks for its presence to
decide what to print.

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage7_human_in_loop/main.py
```

Ask a research question, read the printed plan, then answer the
`Approve this plan? (y/n):` prompt. `y` runs the full Stage 6 research loop
and prints a final answer; `n` stops immediately with no research done.
Type `exit` or `quit` to leave.

## What changed vs. Stage 6

- Added one node, `human_approval`, and one field on `PlannerState`
  (`approved: bool`) to carry its decision to the new conditional edge,
  `route_after_approval`.
- The graph is now compiled with `checkpointer=MemorySaver()` — Stage 6 had
  no checkpointer since nothing paused mid-run; `interrupt()`/
  `Command(resume=...)` require one.
- `main()` now calls a small helper, `run_until_settled`, that loops
  `graph.invoke` -> check for `"__interrupt__"` -> prompt -> `Command(resume=...)`
  until the graph produces a real result instead of pausing again.
- Everything else (`plan`, `research_subtask`, `has_more_subtasks`,
  `synthesize`, the LLM setup) is unchanged from Stage 6 — duplicated here
  rather than shared, per the project's no-shared-abstraction rule.
