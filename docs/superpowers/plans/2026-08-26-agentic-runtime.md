# Agentic Runtime — Implementation Plan (Phase 3A of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the working multi-agent graph into an operable one: honest trace status, graph-level error recovery, parallel subtask fan-out, a bounded rate limiter, and SSE streaming.

**Architecture:** Builds directly on Phase 2's async conversion. The sequential `current_index` loop becomes a `Send`-based fan-out with reducer-merged state; a failing specialist degrades to a recorded failure instead of losing the whole run; a new `/chat/stream` endpoint surfaces progress via `astream_events` without changing any existing route.

**Tech Stack:** Python 3.13, LangGraph (`Send`, `astream_events`), FastAPI (`StreamingResponse`), psycopg async pool.

**Spec:** This plan. Behavior baseline is the 56 currently-passing tests.

## Global Constraints

- **No existing route may change.** Additive only. `/chat`, `/approve`, `/reject`, `/documents*`, `/health` keep their paths, methods, request/response shapes, status codes, and error strings.
- **`tests/test_security_guardrails.py` (17 checks) must stay green.** The security layer is not in scope and must not regress.
- **Never modify `stages/`.** `git status --short stages/` empty at the end.
- **Observability and evals are Phase 3B** — do not add LangSmith, OTel, or an eval harness here.
- Every new node must be async and must not block the event loop.

## One test is deliberately relaxed, with justification

`tests/test_schema_parity.py::test_same_routes_and_methods` currently asserts the new app's route set **equals** stage 25's. That was exactly right for Phase 1, whose whole purpose was proving a port changed nothing. Task 5 adds `/chat/stream`, which makes strict equality wrong rather than useful.

Relax it to a **superset** assertion: every original route still exists with the same methods, and `components.schemas` still contains every original model unchanged. Do NOT delete the test, and do NOT weaken the schema comparison for shared models — the value being preserved is "no existing contract broke", which stays fully enforced.

---

## Task 1: Honest trace status

Today `app/graphs/planner.py` hardcodes `"status": "completed"` for every subtask, including one whose critic said `retry` and then exhausted `MAX_RETRIES`. The UI shows a clean pass for an answer the critic rejected. That is a correctness bug in what the product *reports*, not just cosmetics.

**Files:** Modify `app/graphs/planner.py`, `app/api/schemas.py`; Test: `tests/test_trace_status.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trace_status.py
from app.graphs.planner import build_trace_entry


def test_passed_subtask_is_completed():
    entry = build_trace_entry(
        subtask="s", result={"next": "research", "verdict": "pass",
                             "retry_count": 0, "tools_used": ["search_web"]},
    )
    assert entry["status"] == "completed"


def test_exhausted_retries_is_not_reported_as_completed():
    """A critic that still says 'retry' after MAX_RETRIES means the answer
    was never accepted. Reporting it as 'completed' tells the user the
    system approved something it did not."""
    entry = build_trace_entry(
        subtask="s", result={"next": "research", "verdict": "retry",
                             "retry_count": 1, "tools_used": []},
    )
    assert entry["status"] == "needs_review"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_trace_status.py -v`
Expected: FAIL — `cannot import name 'build_trace_entry'`

- [ ] **Step 3: Implement**

Extract the trace-entry construction into `build_trace_entry(subtask, result) -> dict`, deriving status from the verdict:

```python
"status": "completed" if result["verdict"] == "pass" else "needs_review",
```

Widen `SubtaskTrace.status` in `app/api/schemas.py` from `Literal["completed"]` to `Literal["completed", "needs_review"]`. This is an additive enum widening — no existing field is removed or renamed, and a `completed` response is byte-identical to today's.

- [ ] **Step 4: Run to verify it passes**, then the schema-parity and security suites.

Run: `.venv/Scripts/python.exe -m pytest tests/test_trace_status.py tests/test_schema_parity.py -v`

- [ ] **Step 5: Commit**

```bash
git commit -am "Report a subtask the critic never accepted as needs_review"
```

---

## Task 2: Graph-level error handling

Today, if a specialist or tool raises, the exception propagates out of `ainvoke()` to the 500 handler and **the entire research run is lost** — every already-completed subtask discarded, despite the checkpointer. One flaky web search destroys a three-subtask run.

**Files:** Modify `app/graphs/planner.py`; Test: `tests/test_graph_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_errors.py
import pytest

from app.graphs import planner


async def test_one_failing_subtask_does_not_lose_the_others(monkeypatch):
    """A specialist raising must degrade to a recorded failure, not abort
    the run. The other subtasks' work is already paid for."""
    calls = {"n": 0}

    async def flaky(payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("upstream exploded")
        return {"messages": [type("M", (), {"content": "ok"})()],
                "next": "research", "verdict": "pass",
                "retry_count": 0, "tools_used": []}

    monkeypatch.setattr(planner.supervisor_critic_graph, "ainvoke", flaky)

    out = await planner.research_subtask(
        {"subtask": "b", "index": 1, "user_id": "u"}
    )
    # first call raised on n==2; drive it directly to assert the shape
    assert out["trace"][0]["status"] == "failed"
    assert out["results"][0][1].startswith("Could not complete")


async def test_failure_entry_does_not_leak_exception_text(monkeypatch):
    async def boom(payload):
        raise RuntimeError("DATABASE_URL=postgres://user:secret@host/db")

    monkeypatch.setattr(planner.supervisor_critic_graph, "ainvoke", boom)
    out = await planner.research_subtask({"subtask": "x", "index": 0, "user_id": "u"})
    serialized = str(out)
    assert "secret" not in serialized and "postgres://" not in serialized
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_graph_errors.py -v`

- [ ] **Step 3: Implement**

Wrap the `supervisor_critic_graph.ainvoke(...)` call in `research_subtask` with `try/except Exception`. On failure, log server-side (never to the client) and return a trace entry with `"status": "failed"`, `"specialist": None`-safe defaults, and a generic user-facing answer string. Add `"failed"` to `SubtaskTrace.status`'s `Literal`.

The error string must not embed the exception text — the same error-hygiene rule `tests/test_security_guardrails.py::test_error_hygiene` already enforces for HTTP responses.

`synthesize` must tolerate a failed subtask: it already `zip`s subtasks with results, so a placeholder answer flows through naturally, but the prompt should mark it so the LLM does not present a failure as a finding.

- [ ] **Step 4: Run to verify it passes**, plus the full suite.

- [ ] **Step 5: Commit**

```bash
git commit -am "Degrade a failing subtask instead of losing the whole run"
```

---

## Task 3: ReAct executor loop (replaces the planned fan-out)

**Decision, 2026-08-26:** the user chose a ReAct loop over the parallel `Send`
fan-out originally specified here. The fan-out design is preserved below the
new task for reference, since it remains the right answer if latency ever
matters more than adaptivity.

**Why this shape and not a pure ReAct agent.** A pure ReAct loop has no plan to
approve — the plan emerges as it goes — which would break `/chat` (returns the
pending plan), `/approve`, `/reject`, and the frontend's approval panel, all of
which Rule 1 forbids changing. So approval stays exactly as it is, and the
ReAct loop replaces only what happens *after* approval: the fixed
`current_index` walk becomes an adaptive reason → act → observe loop, seeded
with the approved subtasks as its initial agenda.

**What this buys:** the executor can finish early once it has enough, or add a
follow-up subtask it discovers mid-run — neither of which a fixed script can
do.

**What it costs, accepted deliberately:** no parallelism (reason→act→observe is
sequential), and an unbounded loop can spiral on an ambiguous question, so a
hard step budget is mandatory, not optional.

**Files:** Modify `app/graphs/planner.py`, `app/config.py`; Test: `tests/test_react_loop.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_react_loop.py
from app.graphs.planner import decide_next_action, ReactDecision


def test_finishes_when_the_agenda_is_empty():
    state = {"agenda": [], "results": [], "step_count": 0, "question": "q"}
    assert decide_next_action(state).action == "finish"


def test_step_budget_is_hard():
    """An adaptive loop that can add work to its own agenda must have a
    ceiling, or an ambiguous question spirals until the request times out."""
    from app.config import MAX_REACT_STEPS

    state = {"agenda": ["still", "more", "work"], "results": [],
             "step_count": MAX_REACT_STEPS, "question": "q"}
    decision = decide_next_action(state)
    assert decision.action == "finish"
    assert decision.reason == "step_budget_exhausted"


def test_continues_while_work_remains_and_budget_allows():
    state = {"agenda": ["a"], "results": [], "step_count": 0, "question": "q"}
    d = decide_next_action(state)
    assert d.action == "research" and d.subtask == "a"
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

- Add `agenda: list[str]` and `step_count: int` to `PlannerState`; `plan()` seeds
  `agenda` with the subtasks it produced and `step_count` to 0.
- `decide_next_action(state) -> ReactDecision` is a **pure function** (no LLM),
  deciding continue-vs-finish from the agenda and the budget. Keeping the
  control-flow decision deterministic and separately testable is what makes the
  loop auditable; the *reasoning* about results still uses the LLM.
- `react_step` node: pops the next agenda item, dispatches it to
  `supervisor_critic_graph.ainvoke` exactly as `research_subtask` does today
  (so Task 2's error handling and Task 1's trace entry both still apply),
  appends the result, increments `step_count`.
- `reflect` node: after each step, an LLM call decides whether the accumulated
  results answer the question. It may return a follow-up subtask to append to
  the agenda, or signal done. It must never remove existing agenda items.
- Conditional edge from `reflect` back to `react_step` or on to `synthesize`,
  routed by `decide_next_action`.
- `MAX_REACT_STEPS: int = 6` in config — roughly double a typical 3-subtask
  plan, leaving room for follow-ups without letting the loop run away.

The trace must record follow-up subtasks the agent added itself, distinguishably
from the ones the human approved — otherwise the approval gate is silently
meaningless, since the agent could research anything it liked after approval.
Add `"origin": "approved" | "agent"` to the trace entry.

- [ ] **Step 4: Run to verify they pass**, then the full suite — `test_app_backend.py`'s end-to-end approve flow is the real regression check.

- [ ] **Step 5: Commit**

```bash
git commit -am "Replace the fixed subtask walk with a bounded ReAct executor loop"
```

---

## Task 3 (superseded): Parallel subtask fan-out

`has_more_subtasks` walks `current_index + 1` strictly sequentially. Three subtasks that share nothing run one after another — the biggest available latency win, and the reason Phase 2 exists.

**Files:** Modify `app/graphs/planner.py`; Test: `tests/test_fanout.py`

**The ordering hazard — read before implementing.** `synthesize` pairs subtasks to results with `zip(state["subtasks"], state["results"])`. Under fan-out, completion order is nondeterministic, so appending results as they finish would silently mis-pair every answer with the wrong subtask. Each result MUST carry its originating index and be sorted before synthesis. This is the one way this task can produce a plausible-looking wrong answer, so it gets its own test.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fanout.py
import asyncio

from app.graphs.planner import fan_out_subtasks, order_results


def test_fan_out_emits_one_send_per_subtask():
    state = {"subtasks": ["a", "b", "c"], "user_id": "u", "approved": True}
    sends = fan_out_subtasks(state)
    assert len(sends) == 3
    assert {s.node for s in sends} == {"research_subtask"}
    assert [s.arg["index"] for s in sends] == [0, 1, 2]


def test_results_are_reordered_to_match_subtasks():
    """Completion order is nondeterministic under fan-out; pairing by
    arrival would mis-attribute every answer."""
    out_of_order = [(2, "third"), (0, "first"), (1, "second")]
    assert order_results(out_of_order) == ["first", "second", "third"]


def test_empty_plan_still_reaches_synthesize():
    assert fan_out_subtasks({"subtasks": [], "user_id": "u", "approved": True}) == "synthesize"
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

- `PlannerState.results` becomes `Annotated[list[tuple[int, str]], operator.add]` and `trace` becomes `Annotated[list[dict], operator.add]`, so LangGraph merges concurrent branch writes instead of last-write-wins. **Without the reducer, parallel writes to a plain list silently drop results.**
- `route_after_approval` returns `fan_out_subtasks(state)` — a list of `Send("research_subtask", {"subtask": s, "index": i, "user_id": ...})` — or `"synthesize"` for an empty plan.
- `research_subtask` takes its subtask from the `Send` payload, not from `state["current_index"]`.
- Delete `has_more_subtasks` and the self-loop conditional edge; add `add_edge("research_subtask", "synthesize")`.
- `synthesize` calls `order_results` before zipping.
- Keep `current_index` in the TypedDict only if something still reads it; otherwise remove it and its reset in `plan()`.

Bound the concurrency: pass `max_concurrency` via the graph config (or cap the fan-out width) so a 10-subtask plan cannot open 10 simultaneous LLM + DB conversations and exhaust the pool. `settings.max_parallel_subtasks: int = 3`.

- [ ] **Step 4: Run to verify it passes**, then the full suite — `test_app_backend.py`'s end-to-end approve flow is the real regression check here.

- [ ] **Step 5: Commit**

```bash
git commit -am "Research independent subtasks in parallel via Send fan-out"
```

---

## Task 4: Bound the rate limiter's key space

`_rate_limit_state` is a dict keyed by `user_id` and IP that nothing ever evicts. A caller cycling fake `user_id`s grows it without limit — a slow memory leak that is also a trivial DoS. Documented as known since Stage 24; Phase 3 is where it gets fixed.

**Files:** Modify `app/security/ratelimit.py`; Test: extend `tests/test_security.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_rate_limit_state_does_not_grow_without_bound():
    from app.security.ratelimit import _rate_limit_state, enforce_rate_limits

    _rate_limit_state.clear()
    for i in range(500):
        await enforce_rate_limits("chat", f"fake-user-{i}", "1.2.3.4", (10, 0.05), (10_000, 60))
    await asyncio.sleep(0.1)
    await enforce_rate_limits("chat", "trigger-sweep", "1.2.3.4", (10, 0.05), (10_000, 60))
    assert len(_rate_limit_state) < 100, (
        f"{len(_rate_limit_state)} stale keys retained - unbounded key space"
    )
```

- [ ] **Step 2: Run to verify it fails** (it will retain ~500 keys)

- [ ] **Step 3: Implement**

Add an amortized sweep: on each call, if more than `_SWEEP_INTERVAL_SECONDS` has elapsed since the last sweep, drop every key whose newest timestamp is older than its window. Keep `_rate_limit_state`'s name and `dict` shape — `tests/conftest.py`'s autouse fixture calls `.clear()` on it.

Do not introduce Redis. The in-process limiter is a documented, deliberate choice; this task makes it *bounded*, not distributed.

- [ ] **Step 4: Run to verify it passes**, plus all 17 security checks.

- [ ] **Step 5: Commit**

```bash
git commit -am "Evict stale rate-limiter keys so the key space stays bounded"
```

---

## Task 5: SSE streaming

`/approve` is one blocking call that can run for minutes; the trace panel populates once, at the end. `astream_events` already exists on the compiled graph — this exposes it.

**Files:** Create `app/api/routers/stream.py`; Modify `app/api/factory.py`, `tests/test_schema_parity.py`; Test: `tests/test_streaming.py`

- [ ] **Step 1: Relax the parity test first, with the justification in a comment**

Change `test_same_routes_and_methods` to assert the original's routes are a **subset** of the new app's, and that every original model in `components.schemas` is still present and unchanged. Comment why: Phase 1 needed exact equality to prove a port; Phase 3 adds routes deliberately, and the property still worth enforcing is "no existing contract broke".

- [ ] **Step 2: Write the failing test**

```python
# tests/test_streaming.py
import json

import httpx

from app.main import app


async def test_stream_emits_events_and_terminates(pg_available, openai_available):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                 timeout=120) as c:
        thread_id = "stream-test-1"
        await c.post("/chat", json={"question": "What is 2+2?",
                                    "thread_id": thread_id, "user_id": "u"})
        events = []
        async with c.stream("POST", "/chat/stream",
                            json={"thread_id": thread_id}) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    kinds = [e["type"] for e in events]
    assert "subtask_started" in kinds
    assert kinds[-1] == "done", f"stream must terminate with done, got {kinds[-1]}"


async def test_stream_never_emits_prompt_or_credentials(pg_available, openai_available):
    """Same error-hygiene rule the blocking routes already enforce."""
    from app.agents.prompts import KNOWLEDGE_SYSTEM_PROMPT
    ...
    assert KNOWLEDGE_SYSTEM_PROMPT[:40] not in body
    assert "OPENAI_API_KEY" not in body and "postgres:postgres@" not in body
```

- [ ] **Step 3: Implement**

`POST /chat/stream` takes a `thread_id` already awaiting approval, resumes it with `Command(resume="y")`, and yields SSE frames from `graph.astream_events(..., version="v2")`. Map LangGraph events to a small, stable vocabulary — `subtask_started`, `specialist_selected`, `tool_called`, `critic_verdict`, `subtask_finished`, `done`, `error` — rather than forwarding raw internals, which would leak prompts and couple the UI to LangGraph's event schema.

Reuse the existing `thread_lock` and rate limiting. Apply the same leak guard to anything emitted. Always terminate the stream with a `done` or `error` frame, including on exception, so a client never hangs.

`/approve` stays exactly as it is — the frontend keeps working untouched.

- [ ] **Step 4: Run to verify it passes**, plus the full suite.

- [ ] **Step 5: Commit**

```bash
git commit -am "Add an SSE streaming endpoint for live research progress"
```

---

## Definition of Done

- [ ] Full suite green: `.venv/Scripts/python.exe -m pytest tests/ -q`
- [ ] All 17 security-guardrail checks pass.
- [ ] `test_schema_parity.py` passes as a superset assertion; no existing route or model changed.
- [ ] A subtask whose critic never accepted the answer reports `needs_review`, not `completed`.
- [ ] A raising specialist yields `status: "failed"` for that subtask and the run still completes.
- [ ] No failure path leaks exception text, prompts, or credentials.
- [ ] Subtasks run concurrently, bounded by `settings.max_parallel_subtasks`, and results are re-ordered to match their subtasks.
- [ ] `_rate_limit_state` stays bounded under 500 distinct keys.
- [ ] `/chat/stream` emits typed events and always terminates.
- [ ] `git status --short stages/` empty.
