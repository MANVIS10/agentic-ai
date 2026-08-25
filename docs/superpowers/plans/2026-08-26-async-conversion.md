# Async Conversion — Implementation Plan (Phase 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Convert `app/` from sync-blocking to async end to end, and replace the single shared database connection with a pool — fixing a real transaction-interleaving bug and removing the 40-thread concurrency ceiling.

**Architecture:** Async all the way down. `AsyncConnectionPool` + `AsyncPostgresSaver` at the bottom, `await llm.ainvoke()` in every graph node, `await graph.ainvoke()` in the routers, `async def` endpoints. CPU-bound work (PDF/DOCX text extraction) stays on a worker thread via `asyncio.to_thread` — moving it into the event loop would block every other request.

**Tech Stack:** Python 3.13, FastAPI, LangGraph (async API), `psycopg[binary,pool]`, pgvector.

**Spec:** This plan. Behavior baseline is the 44 currently-passing tests in `tests/`.

## Why this phase exists

Two concrete defects, not stylistic preference:

1. **Transaction interleaving.** `app/db.py:get_connection()` returns one shared connection. Every endpoint is sync `def`, so FastAPI runs it in a 40-thread pool. `upload_document` opens `with conn.transaction():` — but transactions are per-*connection*, not per-thread. A concurrent request's `execute()` lands **inside** that transaction, so a failed upload's rollback can discard an unrelated write.
2. **Thread starvation.** `/approve` runs the whole subtask loop synchronously: 2-3 subtasks x (specialist + critic + possible retry), each a blocking LLM call with **no timeout**. It also holds a per-thread lock for up to 120s. Forty concurrent approvals exhaust the pool and the 41st request blocks.

## Global Constraints

- **The 44 existing tests are the contract.** They must all still pass. `tests/test_schema_parity.py` in particular must stay green — async endpoints must not change the OpenAPI schema.
- **No behavior change visible to a caller.** Same routes, status codes, response shapes, error strings, prompts, and routing decisions. This phase changes *how* work is scheduled, not *what* it produces.
- **Do not touch `stages/`.** `git status --short stages/` must stay empty.
- **Do not fix Phase 3 items** (hardcoded `status: "completed"`, unbounded `_rate_limit_state` key space, missing observability/evals, sequential subtasks). Sequential-subtask fan-out is explicitly Phase 3 — this phase makes it *possible*, not done.
- Every LLM call gets an explicit timeout. A hung upstream must not hold a request forever.

---

## Task 1: Async connection pool

**Files:** Modify `app/db.py`; Modify `requirements.txt`; Test: `tests/test_db_pool.py`

**Interfaces:**
- `app.db.get_pool() -> AsyncConnectionPool` — module-level pool, opened lazily.
- `app.db.connection()` — async context manager yielding a pooled `AsyncConnection`.
- `app.db.init_schema() -> None` — now `async def`.
- `app.db.get_checkpointer() -> AsyncPostgresSaver`
- `app.db.close_pool() -> None` — async, for lifespan shutdown.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_pool.py
import asyncio

import pytest


@pytest.mark.asyncio
async def test_concurrent_transactions_do_not_interleave(pg_available):
    """The bug this phase fixes: with one shared connection, a rollback in
    one request could discard another request's concurrent write. With a
    pool, each gets its own connection, so a failure is isolated."""
    from app.db import connection

    async def failing_write():
        try:
            async with connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "CREATE TEMP TABLE IF NOT EXISTS t_iso (v int)"
                    )
                    raise RuntimeError("boom")
        except RuntimeError:
            pass

    async def good_write():
        async with connection() as conn:
            row = await (await conn.execute("SELECT 1")).fetchone()
            return row[0]

    results = await asyncio.gather(failing_write(), good_write(), good_write())
    assert results[1] == 1 and results[2] == 1


@pytest.mark.asyncio
async def test_pool_serves_concurrent_callers(pg_available):
    from app.db import connection

    async def ping():
        async with connection() as conn:
            return (await (await conn.execute("SELECT 1")).fetchone())[0]

    assert await asyncio.gather(*(ping() for _ in range(10))) == [1] * 10
```

Add `pytest-asyncio` to `requirements.txt` and `asyncio_mode = auto` to a new `pytest.ini` (or `[tool.pytest.ini_options]`), so `@pytest.mark.asyncio` is unnecessary going forward.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db_pool.py -v`
Expected: FAIL — `ImportError: cannot import name 'connection' from 'app.db'`

- [ ] **Step 3: Implement**

```python
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None


def get_pool() -> AsyncConnectionPool:
    """Replaces Phase 1's single shared connection. Each caller gets its own
    connection for the duration of its work, so `async with conn.transaction()`
    is genuinely isolated - the bug this phase fixes."""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.db_pool_max_size,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            configure=_configure,
        )
    return _pool


async def _configure(conn):
    await register_vector_async(conn)


@asynccontextmanager
async def connection():
    pool = get_pool()
    if pool.closed:
        await pool.open()
    async with pool.connection() as conn:
        yield conn
```

`register_vector` has an async counterpart — import `register_vector_async` from `pgvector.psycopg`. Registering it in the pool's `configure` hook means every pooled connection knows the `vector` type, which the old code did once for its single connection.

Add `db_pool_max_size: int = 10` to `Settings`. Add `psycopg[binary,pool]` and `pytest-asyncio` to `requirements.txt`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db_pool.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/db.py requirements.txt pytest.ini tests/test_db_pool.py
git commit -m "Replace the shared connection with an async pool"
```

---

## Task 2: Async tools

**Files:** Modify `app/tools/document_search.py`, `app/tools/web_search.py`; Test: `tests/test_tools.py` (extend)

**Interfaces:** `search_uploaded_documents` and the web-search tool both gain async implementations. `@tool` supports an async `coroutine`; simplest is to define the function `async def` so LangChain's `ainvoke` path uses it natively.

- [ ] **Step 1: Write the failing test**

```python
async def test_document_search_is_awaitable():
    from app.tools.document_search import search_uploaded_documents

    assert search_uploaded_documents.coroutine is not None, (
        "tool must expose an async implementation so ToolNode.ainvoke does not "
        "fall back to running it on a worker thread"
    )


async def test_document_search_still_hides_user_id():
    """Regression guard: the Stage 23 isolation property must survive the
    async rewrite."""
    from app.tools.document_search import search_uploaded_documents

    assert "user_id" not in search_uploaded_documents.args
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tools.py -v`
Expected: FAIL on `coroutine is not None`

- [ ] **Step 3: Implement**

`search_uploaded_documents` becomes `async def`, using `await embeddings.aembed_query(query)` and the pooled `async with connection() as conn`. The SQL, the `KNOWLEDGE_TOOL_K` limit, the untrusted-content envelope, and the `user_id` filter are all unchanged — only the I/O calls become awaited.

`DuckDuckGoSearchRun` is a sync library. Wrap its call in `await asyncio.to_thread(...)` rather than blocking the loop.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools tests/test_tools.py
git commit -m "Make the document-search and web-search tools async"
```

---

## Task 3: Async agents and graph nodes

**Files:** Modify `app/agents/{research,knowledge,analysis,supervisor,critic}.py`, `app/graphs/{specialist,planner}.py`; Test: `tests/test_graphs.py` (extend)

**Interfaces:** Every node function becomes `async def` and awaits its LLM call (`await *_llm.ainvoke(...)`). `supervisor_critic_graph` is invoked with `await ...ainvoke(...)` inside `research_subtask`. `build_graph(checkpointer)` is unchanged in signature.

- [ ] **Step 1: Write the failing test**

```python
import inspect


def test_all_graph_nodes_are_coroutines():
    """A sync node inside an async graph silently blocks the event loop -
    the failure mode is a latency cliff under load, not an error, so assert
    it structurally."""
    from app.graphs import planner
    from app.agents import supervisor, critic

    for fn in (planner.plan, planner.synthesize, planner.research_subtask,
               supervisor.supervisor_node, critic.critic_node):
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} is still sync"


def test_specialist_graph_topology_is_unchanged():
    """The async rewrite must not alter routing."""
    from app.graphs.specialist import supervisor_critic_graph

    nodes = set(supervisor_critic_graph.get_graph().nodes)
    assert {"supervisor", "research_agent", "knowledge_agent",
            "analysis_agent", "critic"} <= nodes
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_graphs.py -v`
Expected: FAIL — `plan is still sync`

- [ ] **Step 3: Implement**

Convert each node to `async def` and `await` its LLM call. `route_*` functions stay sync — they are pure predicates over state, not I/O, and LangGraph accepts sync conditional edges.

**Do not** change `has_more_subtasks` or the sequential `current_index + 1` loop. Parallel fan-out is Phase 3.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_graphs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agents app/graphs tests/test_graphs.py
git commit -m "Convert agent nodes and graphs to async"
```

---

## Task 4: Async locks and rate limiter

**Files:** Modify `app/security/locks.py`, `app/security/ratelimit.py`; Test: `tests/test_security.py` (extend)

**Interfaces:** `locks.thread_lock(thread_id)` becomes an `@asynccontextmanager`. `ratelimit.enforce_rate_limits(...)` becomes `async def`.

Rationale: `threading.Lock.acquire(timeout=...)` blocks the event loop. In async, one coroutine holding it would stall every other request in the process — strictly worse than the threaded version. `asyncio.Lock` + `asyncio.wait_for` yields instead.

- [ ] **Step 1: Write the failing test**

```python
async def test_same_thread_id_serialises_but_different_ids_do_not():
    import asyncio
    from app.security.locks import thread_lock

    order = []

    async def worker(tid, tag):
        async with thread_lock(tid):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.05)
            order.append(f"{tag}-out")

    await asyncio.gather(worker("a", "a1"), worker("a", "a2"), worker("b", "b1"))
    # the two "a" workers must not interleave with each other
    a = [x for x in order if x.startswith("a")]
    assert a in (["a1-in","a1-out","a2-in","a2-out"],
                 ["a2-in","a2-out","a1-in","a1-out"])


async def test_busy_thread_raises_409_not_forever():
    import asyncio
    import pytest
    from fastapi import HTTPException
    from app.security import locks

    async with locks.thread_lock("busy"):
        with pytest.raises(HTTPException) as exc:
            async with locks.thread_lock("busy", timeout=0.05):
                pass
    assert exc.value.status_code == 409
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_security.py -v`
Expected: FAIL — `thread_lock` is not an async context manager

- [ ] **Step 3: Implement**

`_thread_locks` becomes `dict[str, asyncio.Lock]`, guarded by an `asyncio.Lock`. `thread_lock` gains an optional `timeout` parameter defaulting to `THREAD_LOCK_TIMEOUT_SECONDS`, and raises the same 409 with the same `detail` string on timeout. `_rate_limit_state` keeps its dict shape and its `_rate_limit_state.clear()` surface — `tests/conftest.py`'s `reset_rate_limiter` fixture depends on it.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_security.py tests/test_security_guardrails.py -v`
Expected: PASS — including all 16 security-guardrail tests.

- [ ] **Step 5: Commit**

```bash
git add app/security tests/test_security.py
git commit -m "Convert per-thread locking and rate limiting to asyncio"
```

---

## Task 5: Async ingestion and routers

**Files:** Modify `app/ingestion/{extract,store}.py`, `app/api/routers/{health,chat,documents}.py`, `app/api/factory.py`; Test: existing suites

**Interfaces:** All route handlers become `async def`. `extract_text_with_timeout` becomes `async def` using `asyncio.to_thread` + `asyncio.wait_for`. `store.embed_and_store` becomes async and takes a pooled connection.

- [ ] **Step 1: Confirm the contract test is green before touching routers**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schema_parity.py -v`
Expected: PASS. This is the guard for the whole task — re-run it after every router change.

- [ ] **Step 2: Convert the routers**

Each handler: `def` -> `async def`, `_graph.invoke(...)` -> `await _graph.ainvoke(...)`, `_graph.get_state(...)` -> `await _graph.aget_state(...)`, `enforce_rate_limits(...)` -> `await ...`, `with thread_lock(...)` -> `async with thread_lock(...)`, and every `conn.execute` behind `async with connection() as conn`.

**Critical — preserve upload atomicity.** The original wrapped the `documents` row insert AND the chunk inserts in ONE transaction. Keep exactly that shape:

```python
async with connection() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO documents ...", ...)
        await embed_and_store(conn, document_id, chunks)
```
With a pool this is now genuinely isolated, which is the point of the phase.

**Extraction stays off the loop.** `extract_text` parses PDFs and DOCX — CPU-bound and potentially slow. Run it via `await asyncio.wait_for(asyncio.to_thread(extract_text, data, ftype), timeout=EXTRACTION_TIMEOUT_SECONDS)`, preserving the existing timeout semantics and the generic 422 that all dangerous-file rejections collapse into.

`factory.py`'s lifespan handler becomes `await init_schema()` on startup and `await close_pool()` on shutdown.

- [ ] **Step 3: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all previously-passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add app/ tests/
git commit -m "Convert the ingestion pipeline and HTTP routers to async"
```

---

## Task 6: LLM timeouts

**Files:** Modify `app/llm.py`, `app/config.py`; Test: `tests/test_llm_timeout.py`

Currently no LLM call has a timeout. A hung upstream holds a request — and its per-thread lock — indefinitely.

- [ ] **Step 1: Write the failing test**

```python
def test_every_llm_has_a_timeout_and_bounded_retries():
    from app import llm

    for name in ("chat_llm", "research_llm", "knowledge_llm", "analysis_llm"):
        model = getattr(llm, name)
        assert model.request_timeout is not None, f"{name} has no timeout"
        assert model.max_retries <= 2, f"{name} retries too many times"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_timeout.py -v`
Expected: FAIL — `chat_llm has no timeout`

- [ ] **Step 3: Implement**

Add `llm_request_timeout_seconds: float = 60.0` and `llm_max_retries: int = 2` to `Settings`; pass `request_timeout=` and `max_retries=` to every `ChatOpenAI(...)`. `supervisor_llm` and `critic_llm` keep `.with_structured_output(..., method="function_calling")` — that must not change, or gpt-4o-mini starts echoing the schema and crashes a dict lookup.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_timeout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/llm.py app/config.py tests/test_llm_timeout.py
git commit -m "Give every LLM call an explicit timeout and bounded retries"
```

---

## Task 7: Prove the concurrency win

**Files:** Create `tests/test_concurrency.py`

The point of this phase is throughput. Assert it, or the work is unverified.

- [ ] **Step 1: Write the test**

```python
import asyncio
import time

import httpx
import pytest

from app.main import app


async def test_independent_requests_do_not_serialise(pg_available):
    """Ten concurrent /health calls must overlap, not queue. Under the old
    sync handler each occupied a pool thread; under async they share the
    loop."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        start = time.perf_counter()
        results = await asyncio.gather(*(c.get("/health") for _ in range(10)))
        elapsed = time.perf_counter() - start

    assert all(r.status_code == 200 for r in results)
    assert elapsed < 2.0, f"10 concurrent health checks took {elapsed:.1f}s"
```

Add `httpx` to `requirements.txt` if not already present (FastAPI's TestClient depends on it, so it likely is).

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe -m pytest tests/test_concurrency.py -v`
Expected: PASS

- [ ] **Step 3: Full-suite regression + commit**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: every previously-passing test still passes.

```bash
git add tests/test_concurrency.py
git commit -m "Add concurrency regression tests for the async conversion"
```

---

## Definition of Done

- [ ] All 44 pre-existing tests still pass, plus the new async/concurrency tests.
- [ ] `tests/test_schema_parity.py` passes — the HTTP contract is unchanged.
- [ ] `tests/test_security_guardrails.py` passes — all 16 security checks survive.
- [ ] No `psycopg.connect(` remains in `app/`; `grep -rn "psycopg.connect(" app/` is empty.
- [ ] No sync `def` route handler remains in `app/api/routers/`.
- [ ] Every `ChatOpenAI(...)` passes an explicit `request_timeout`.
- [ ] `git status --short stages/` is empty.
- [ ] Upload still writes the documents row and its chunks in ONE transaction.
- [ ] Phase 3 items untouched: subtasks still run sequentially, `status: "completed"` still hardcoded, `_rate_limit_state` key space still unbounded.
