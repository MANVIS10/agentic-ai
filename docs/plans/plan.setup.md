# Plan: Rebuild Stage 7 to match `docs/specs/spec_document.md`

## Context

`docs/specs/spec_document.md` was added to the repo as the project's
governing spec and describes Stage 7 ("Human-in-the-Loop") as: **Planner ->
show plan -> human approves/rejects the whole plan once -> approve
continues to research, reject stops the run**. Earlier this session, before
this spec was read, a `stages/stage7_human_in_loop/` was already built and
shipped with a *different* design: per-tool-call y/n approval wrapped
around Stage 2's web-search agent (reject there just skipped that one tool
call; the LLM kept going).

The user confirmed directly: rebuild Stage 7 to match the spec exactly
(planner -> show plan -> interrupt -> approve continues / reject stops),
keep it minimal and beginner-friendly, don't touch Stages 1-6, don't add
any new tools/dependencies, and update Stage 7's README plus `PROGRESS.md`
to match. This plan also corrects the top-level `README.md` and
`CLAUDE.md`, since both currently describe the old (now-wrong) Stage 7
design in their roadmap tables/prose and would otherwise be left stale.

This rebuild **replaces** `stages/stage7_human_in_loop/main.py` and its README
in place — same folder and filenames, new contents. It does not create a
second folder, and it does not touch `stage1`-`stage6`.

## Design

Reuses Stage 6's exact planning pattern (`PlannerState`, `plan`,
`research_subtask`, `has_more_subtasks`, `synthesize` — all unchanged) and
inserts one new node, `human_approval`, between `plan` and
`research_subtask`. No tool is bound in this stage, same as Stage 6.

**State** — Stage 6's `PlannerState` plus one new field to carry the
human's decision from the node to the routing function:

```python
class PlannerState(TypedDict):
    question: str
    subtasks: list[str]
    current_index: int
    results: list[str]
    final_answer: str
    approved: bool
```

**Nodes**
- `plan` — identical to Stage 6: LLM call breaks the question into 2-3
  subtasks, **prints them** (this print is what satisfies the spec's "Show
  plan" step — it happens before the graph reaches `human_approval`, so no
  need to re-display the plan a second time in the interrupt payload).
- `human_approval` (new) —
  ```python
  def human_approval(state: PlannerState):
      decision = interrupt("Approve this plan? (y/n): ")
      return {"approved": decision.strip().lower() == "y"}
  ```
- `research_subtask`, `synthesize` — identical to Stage 6.

**Routing**
```python
def route_after_approval(state: PlannerState) -> str:
    return "research_subtask" if state["approved"] else END
```

**Edges**
```
START -> plan -> human_approval
human_approval --(route_after_approval)--> research_subtask | END
research_subtask --(has_more_subtasks, unchanged)--> research_subtask | synthesize
synthesize -> END
```
Compiled with `checkpointer=MemorySaver()` — required for `interrupt()` /
`Command(resume=...)` to pause and resume in place (same requirement as
the old Stage 7, already proven working against the installed langgraph
1.2.11).

**Reject -> Stop semantics**: routes straight to `END` via the conditional
edge, no dedicated "cancelled" node — `final_answer` is simply never set.
`main()` checks for its presence to decide what to print. This mirrors
Stage 6's terse conditional-routing style (`has_more_subtasks` already
returns a node name or the next step directly, no extra state).

**REPL / `main()`** — reuses the `run_until_settled` invoke-loop shape
already proven in the old Stage 7 (call `graph.invoke`, check for
`"__interrupt__"` in the result, prompt with `input()`, resume via
`Command(resume=...)`, repeat until a normal result comes back):

```python
def run_until_settled(initial_input, config):
    result = graph.invoke(initial_input, config=config)
    while "__interrupt__" in result:
        prompt = result["__interrupt__"][0].value
        answer = input(f"{prompt} ").strip()
        result = graph.invoke(Command(resume=answer), config=config)
    return result


def main():
    config = {"configurable": {"thread_id": "1"}}
    print("Stage 7 human-in-the-loop planner. Type 'exit' to quit.\n")

    while True:
        question = input("Research question: ").strip()
        if question.lower() in {"exit", "quit"}:
            break

        result = run_until_settled({"question": question}, config)

        if "final_answer" in result:
            print(f"\nFinal answer: {result['final_answer']}\n")
        else:
            print("\nPlan was not approved — no research was done.\n")
```

No new pip dependencies — `interrupt`, `Command`, `MemorySaver` are already
used elsewhere in this repo.

## Files to change

- **`stages/stage7_human_in_loop/main.py`** — full rewrite per the design above.
- **`stages/stage7_human_in_loop/README.md`** — full rewrite: new flow diagram
  (`plan -> show plan -> human_approval -> approve/reject`), architecture
  section describing the 4-node graph, "concept demonstrated" section
  (interrupt/resume — still accurate, just re-explained against the new
  flow), "what changed vs. Stage 6" (not vs. Stage 2 — this build's
  comparison point changes since it now extends the planner, not the tool
  agent).
- **`PROGRESS.md`** — Stage 7 table row (tool column becomes "— (no tool;
  reuses Stage 6's planner)", description becomes plan-approval, not
  tool-approval); "Current tool" paragraph; the Stage 7 "what I learned"
  bullet (rewrite: interrupt pauses for whole-plan approval, not
  per-tool-call; reject routes straight to `END`); the Stage 7 "important
  decisions" bullets (drop the DuckDuckGo/Stage-2-reuse framing, replace
  with "reuses Stage 6's planner state/nodes, not Stage 2's tool agent";
  keep the "dynamic `interrupt()` over `interrupt_before=`" reasoning,
  still accurate).
- **`README.md`** (top-level) — Stage 7 row in the stages table (tool/
  concept columns) and the prose paragraph describing Stage 7 under the
  "long-term target concept progression" section.
- **`CLAUDE.md`** — item 7 in the numbered stage list description, and the
  "Each tool-using stage binds exactly one tool to its agent" paragraph
  (Stage 7 no longer binds a tool at all — needs to move to the same
  "uses plain LLM calls with no bound tool" framing already used for
  Stage 6).

`spec_document.md` itself is intentionally left unchanged — it's the
external spec baseline, and the spec's own "after coding" checklist only
calls for updating `PROGRESS.md` and the stage README, not the spec file.
`stage1`-`stage6` are not touched.

## Verification

1. Run `python stages/stage7_human_in_loop/main.py`, ask a research question,
   confirm the plan prints, confirm the approval prompt appears and the
   graph is genuinely paused (not just delayed) waiting on `input()`.
2. Answer `y` — confirm it proceeds through `research_subtask` for each
   listed subtask and prints a `Final answer:`.
3. Ask another question in the same run, answer `n` at the approval
   prompt — confirm it prints "Plan was not approved — no research was
   done." and does **not** call the LLM for any subtask research.
4. Confirm `exit`/`quit` still cleanly ends the REPL.
5. Spot-check `stage1`-`stage6` folders are byte-identical to before
   (`git diff` if the repo is under version control, otherwise a manual
   check) — this rebuild must not touch them.
