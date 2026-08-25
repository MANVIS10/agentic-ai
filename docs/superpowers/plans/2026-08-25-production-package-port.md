# Production Package Port — Implementation Plan (Phase 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `stage25_react_ui/backend/main.py` (1749 lines, one file) into a production-style `app/` package with zero behavior change.

**Architecture:** A new top-level `app/` package. `stage25_react_ui/` is NEVER touched — it stays frozen as the learning snapshot, so nothing that works today can break. The port is *mechanical*: code moves between files, but no logic, prompt, constant, route path, response shape, or status code changes. Correctness is proven by asserting the new app's OpenAPI schema is identical to the old one's, then re-running the existing Stage 25 + Stage 24 test suites against the new app.

**Tech Stack:** Python 3.13, FastAPI, LangGraph, psycopg3, pgvector, pydantic-settings (new dep), pytest (new dev dep).

**Spec:** This plan. The source of truth for behavior is `stage25_react_ui/backend/main.py` itself.

## Global Constraints

- **Do not modify anything under `stage25_react_ui/`** — not one byte. Same for `stage1_*` … `stage24_*`.
- **No behavior change.** Every route path, HTTP method, status code, response model, field name, prompt string, numeric constant, and error `detail` string is preserved verbatim. Move strings; never retype them.
- **No new logic.** Do not "improve" error handling, add validation, rename for clarity, or fix the known bugs (shared connection, unbounded dicts, hardcoded `status: "completed"`). Those are Phase 2/3. Add a comment naming the phase; do not act.
- **Async conversion is Phase 2.** Every endpoint stays `def`, not `async def` (the two existing `async def` middlewares stay async).
- **Connection pooling is Phase 2.** Phase 1 keeps the single `psycopg.connect(...)`, moved behind `app/db.py` but still one connection.
- Python 3.13. Preserve the existing `X | None` typing style; no `from __future__` imports.
- Every module opens with a docstring naming the original line range it came from.

---

## Target File Structure

```
app/
├── __init__.py
├── config.py                    # Settings: env vars + every module-level constant
├── db.py                        # lazy connection, init_schema(), register_vector
├── llm.py                       # the 6 ChatOpenAI instances + embeddings
├── tools/
│   ├── __init__.py
│   ├── web_search.py            # research agent's DuckDuckGo tool
│   ├── document_search.py       # search_uploaded_documents (+ untrusted envelope)
│   └── calculator.py            # calculate, _eval_node, allowed-op tables
├── agents/
│   ├── __init__.py
│   ├── prompts.py               # all 5 *_SYSTEM_PROMPT constants
│   ├── research.py              # research_agent_node + research_graph
│   ├── knowledge.py             # KnowledgeState, knowledge_agent_node + knowledge_graph
│   ├── analysis.py              # analysis_agent_node + analysis_graph
│   ├── supervisor.py            # Route, supervisor_node, route_from_supervisor
│   └── critic.py                # Review, critic_node, route_from_critic
├── graphs/
│   ├── __init__.py
│   ├── specialist.py            # CriticState, the 3 node wrappers, supervisor_critic_graph
│   └── planner.py               # PlannerState, plan/human_approval/research_subtask/synthesize
├── ingestion/
│   ├── __init__.py
│   ├── extract.py               # get_file_type, extract_text, extract_text_with_timeout
│   └── store.py                 # chunk -> embed -> persist helpers
├── security/
│   ├── __init__.py
│   ├── validation.py            # validate_text_field + input limits
│   ├── locks.py                 # _lock_for, thread_lock
│   ├── ratelimit.py             # _enforce_rate_limit, enforce_rate_limits
│   └── leakguard.py             # leaks_system_prompt
├── api/
│   ├── __init__.py
│   ├── schemas.py               # all 13 pydantic models
│   ├── middleware.py            # limit_json_body_size, unhandled_exception_handler
│   ├── factory.py               # create_app()
│   └── routers/
│       ├── __init__.py
│       ├── health.py            # GET /health
│       ├── chat.py              # POST /chat, /approve, /reject
│       └── documents.py         # GET /documents, POST upload/search/backfill-embeddings
└── main.py                      # app = create_app() + uvicorn entry
tests/
├── conftest.py
├── test_schema_parity.py        # proves the port is faithful
└── test_app_backend.py          # port of stage25's test file
```

### Source line map (original `stage25_react_ui/backend/main.py`)

| Original lines | Destination |
|---|---|
| 1-97 (docstring, imports) | split across modules; summary -> `app/__init__.py` |
| 101-115 (`DATABASE_URL`, `ALLOWED_ORIGINS`, `MAX_RETRIES`, `llm`) | `config.py` + `llm.py` |
| 119-154 (Research Agent) | `agents/prompts.py`, `tools/web_search.py`, `agents/research.py` |
| 157-281 (Knowledge Agent) | `agents/prompts.py`, `tools/document_search.py`, `agents/knowledge.py` |
| 283-303 (leak guard) | `security/leakguard.py` |
| 306-394 (Analysis Agent) | `tools/calculator.py`, `agents/prompts.py`, `agents/analysis.py` |
| 397-600 (Supervisor + Critic) | `agents/supervisor.py`, `agents/critic.py`, `graphs/specialist.py` |
| 603-753 (Planner + approval) | `graphs/planner.py` |
| 742-799 (checkpointer, DDL, pgvector) | `db.py` |
| 803-927 (upload pipeline) | `ingestion/extract.py`, `config.py` |
| 910-940 (validation constants) | `security/validation.py`, `config.py` |
| 943-1003 (thread locks) | `security/locks.py` |
| 1006-1082 (rate limiting) | `security/ratelimit.py` |
| 1093-1202 (pydantic models) | `api/schemas.py` |
| 1205-1263 (FastAPI app, CORS, middleware) | `api/factory.py`, `api/middleware.py` |
| 1266-1280 (`/health`) | `api/routers/health.py` |
| 1282-1436 (`/chat`, `/approve`, `/reject`) | `api/routers/chat.py` |
| 1438-1749 (`/documents*`) | `api/routers/documents.py` |

### Import-cycle rules

- Direction is strictly one-way: `config` -> `db`/`llm` -> `tools` -> `agents` -> `graphs` -> `api`.
- `security/*` may be imported by anything; it imports only `config`.
- If a cycle appears, move the shared symbol **downward** (toward `config`). Never add a function-level import to break one.
- `search_uploaded_documents` uses `InjectedState("user_id")` — a string key — so it does **not** import `KnowledgeState`. Do not create that import.

---

## Task 1: Package skeleton, config, and dependency

**Files:**
- Create: `app/__init__.py`, `app/config.py`
- Modify: `requirements.txt`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.config.settings` — a module-level `Settings` instance with `database_url: str`, `allowed_origins: list[str]`, `openai_chat_model: str`, `openai_embedding_model: str`; plus plain module constants for everything the original had at module scope (listed in Step 3).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from app.config import settings, MAX_RETRIES, MAX_FILE_SIZE_BYTES, CHUNK_SIZE


def test_defaults_match_original_module_constants():
    assert MAX_RETRIES == 1
    assert MAX_FILE_SIZE_BYTES == 20 * 1024 * 1024
    assert CHUNK_SIZE == 400
    assert settings.openai_chat_model == "gpt-4o-mini"
    assert settings.openai_embedding_model == "text-embedding-3-small"


def test_localhost_dev_origin_always_allowed():
    assert "http://localhost:5173" in settings.allowed_origins


def test_importing_config_opens_no_database_connection():
    import app.config  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Create the package and config**

`app/config.py` holds a `pydantic_settings.BaseSettings` subclass for the env-driven values, plus **every** other module-level constant from the original, moved verbatim:

`MAX_RETRIES`, `KNOWLEDGE_TOOL_K`, `UNTRUSTED_CONTENT_PREFIX`, `UNTRUSTED_CONTENT_SUFFIX`, `LEAK_GUARD_MIN_SPAN`, `LEAK_GUARD_FALLBACK_ANSWER`, `ALLOWED_FILE_TYPES`, `MAX_FILE_SIZE_BYTES`, `MAX_FILENAME_LENGTH`, `MAX_PDF_PAGES`, `MAX_DOCX_UNCOMPRESSED_BYTES`, `EXTRACTION_TIMEOUT_SECONDS`, `CORRUPT_FILE_DETAIL`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `MAX_TEXT_INPUT_LENGTH`, `MAX_TOP_K`, `MAX_JSON_BODY_BYTES`, `THREAD_LOCK_TIMEOUT_SECONDS`, `RATE_LIMIT_DETAIL`, and all eight `*_RATE_LIMIT` tuples.

```python
"""Every module-level constant from stage25_react_ui/backend/main.py
(lines 101-115, 184-200, 289-290, 812-823, 937-939, 967, 1035-1043),
gathered in one place. Values are copied verbatim - changing any of them
is a behavior change, which Phase 1 forbids."""

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5433/postgres?sslmode=disable",
        alias="DATABASE_URL",
    )
    # Comma-separated extra origins for deployment, on top of the Vite dev server.
    allowed_origins_env: str = Field(default="", alias="ALLOWED_ORIGINS")
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    @property
    def allowed_origins(self) -> list[str]:
        return ["http://localhost:5173"] + [
            o.strip() for o in self.allowed_origins_env.split(",") if o.strip()
        ]


settings = Settings()

MAX_RETRIES = 1  # at most one retry per subtask - two specialist attempts total
# ... remaining constants copied verbatim from the original
```

Add `pydantic-settings` and `pytest` to `requirements.txt`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pip install pydantic-settings pytest && .venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/__init__.py app/config.py tests/test_config.py requirements.txt
git commit -m "Add app package skeleton and centralized config"
```

---

## Task 2: Database and LLM modules

**Files:**
- Create: `app/db.py`, `app/llm.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `app.config.settings`.
- Produces:
  - `app.db.get_connection() -> psycopg.Connection` — returns the module-level connection, opening it on first call (lazy, NOT at import).
  - `app.db.init_schema() -> None` — runs `checkpointer.setup()`, the two `CREATE TABLE`s, the `ALTER TABLE`/index, `CREATE EXTENSION vector`, the embedding column, and `register_vector`. Idempotent.
  - `app.db.get_checkpointer() -> PostgresSaver`
  - `app.llm.chat_llm`, `research_llm`, `knowledge_llm`, `analysis_llm`, `supervisor_llm`, `critic_llm`, `embeddings` — the same six `ChatOpenAI` instances and one `OpenAIEmbeddings` as the original (lines 115, 136, 182, 264, 376, 442, 535). `supervisor_llm` and `critic_llm` keep `.with_structured_output(..., method="function_calling")` — the default `json_schema` mode makes gpt-4o-mini echo the schema and crash a dict lookup.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import importlib


def test_import_does_not_connect(monkeypatch):
    """The original connected to Postgres at import time (main.py:749).
    The port must be import-safe: connecting happens in get_connection()."""
    import psycopg

    def explode(*a, **kw):
        raise AssertionError("connected at import time")

    monkeypatch.setattr(psycopg, "connect", explode)
    import app.db

    importlib.reload(app.db)  # must not raise


def test_init_schema_is_idempotent(pg_available):
    from app.db import init_schema

    init_schema()
    init_schema()  # second call must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write the implementation**

Move lines 749-799 into `app/db.py`, making the connect lazy:

```python
_conn: psycopg.Connection | None = None
_checkpointer: PostgresSaver | None = None


def get_connection() -> psycopg.Connection:
    """One shared autocommit connection, exactly as the original had at
    main.py:749. Phase 2 replaces this with a ConnectionPool - the shared
    connection is a known concurrency hazard (a `with conn.transaction()`
    in one thread captures another thread's execute), preserved here
    deliberately because Phase 1 forbids behavior change."""
    global _conn
    if _conn is None:
        _conn = psycopg.connect(
            settings.database_url, autocommit=True, prepare_threshold=0
        )
    return _conn
```

`init_schema()` holds the DDL strings and `register_vector(conn)` verbatim.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/llm.py tests/test_db.py
git commit -m "Add lazy database module and per-role LLM module"
```

---

## Task 3: Tools

**Files:**
- Create: `app/tools/__init__.py`, `web_search.py`, `document_search.py`, `calculator.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `app.config`, `app.db.get_connection`, `app.llm.embeddings`.
- Produces: `search_web` (the `DuckDuckGoSearchRun` tool from lines 119-138); `search_uploaded_documents` (line 215, `@tool`, signature `(query: str, user_id: Annotated[str, InjectedState("user_id")]) -> str`); `calculate` (line 344, `@tool`, `(expression: str) -> str`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools.py
import pytest
from app.tools.calculator import calculate


@pytest.mark.parametrize("expr,expected", [("2 + 2", "4"), ("10 / 4", "2.5")])
def test_calculate_evaluates_arithmetic(expr, expected):
    assert expected in calculate.invoke({"expression": expr})


def test_calculate_rejects_names_and_calls():
    assert "error" in calculate.invoke({"expression": "__import__('os')"}).lower()


def test_document_search_tool_hides_user_id_from_the_llm():
    """InjectedState must keep user_id out of the schema the model fills -
    this is the Stage 23 per-user isolation guarantee."""
    from app.tools.document_search import search_uploaded_documents

    assert "user_id" not in search_uploaded_documents.args_schema.model_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools'`

- [ ] **Step 3: Write the implementation**

Move lines 311-362 (`_ALLOWED_BINOPS`, `_ALLOWED_UNARYOPS`, `_eval_node`, `calculate`) to `calculator.py`; lines 125-138's search tool to `web_search.py`; lines 215-260 to `document_search.py`, replacing bare `pg_conn` with `get_connection()` and importing `UNTRUSTED_CONTENT_PREFIX` / `UNTRUSTED_CONTENT_SUFFIX` / `KNOWLEDGE_TOOL_K` from config.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tools.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/tools tests/test_tools.py
git commit -m "Extract the three agent tools into app/tools"
```

---

## Task 4: Agents, prompts, and the leak guard

**Files:**
- Create: `app/agents/__init__.py`, `prompts.py`, `research.py`, `knowledge.py`, `analysis.py`, `supervisor.py`, `critic.py`
- Create: `app/security/__init__.py`, `app/security/leakguard.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Produces: `research_graph`, `knowledge_graph`, `analysis_graph` (compiled subgraphs); `KnowledgeState`; `Route` and `Review` TypedDicts; `supervisor_node`, `critic_node`, `route_from_supervisor`, `route_from_critic`; `leakguard.leaks_system_prompt(answer: str) -> bool`.
- Prompt constants keep their exact original names: `RESEARCH_SYSTEM_PROMPT`, `KNOWLEDGE_SYSTEM_PROMPT`, `ANALYSIS_SYSTEM_PROMPT`, `SUPERVISOR_SYSTEM_PROMPT`, `CRITIC_SYSTEM_PROMPT`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agents.py
from pathlib import Path

from app.agents import prompts
from app.config import LEAK_GUARD_MIN_SPAN
from app.security.leakguard import leaks_system_prompt

ORIGINAL = Path("stage25_react_ui/backend/main.py").read_text(encoding="utf-8")


def test_prompts_did_not_drift_from_the_frozen_stage():
    """Prompt drift is a silent behavior change. Every substantial line of
    each ported prompt must still appear in the original file."""
    for name in (
        "RESEARCH_SYSTEM_PROMPT",
        "KNOWLEDGE_SYSTEM_PROMPT",
        "ANALYSIS_SYSTEM_PROMPT",
        "SUPERVISOR_SYSTEM_PROMPT",
        "CRITIC_SYSTEM_PROMPT",
    ):
        ported = getattr(prompts, name)
        assert ported.strip(), f"{name} is empty"
        for line in (ln.strip() for ln in ported.splitlines()):
            if len(line) > 20:
                assert line in ORIGINAL, f"{name} drifted: {line!r}"


def test_leak_guard_catches_verbatim_span():
    span = prompts.KNOWLEDGE_SYSTEM_PROMPT[: LEAK_GUARD_MIN_SPAN + 10]
    assert leaks_system_prompt(f"Sure, here it is: {span}")


def test_leak_guard_allows_normal_answers():
    assert not leaks_system_prompt("The document says revenue grew 12%.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents'`

- [ ] **Step 3: Write the implementation**

Move the five prompt constants to `prompts.py` by cutting and pasting the string literals — never retyping. Move each `*_agent_node` plus its `StateGraph` wiring into its own module. Move lines 293-303 (`_leaks_system_prompt`) to `leakguard.py` as public `leaks_system_prompt` — the only rename this plan permits, because the name now crosses a module boundary. Behavior identical.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents app/security tests/test_agents.py
git commit -m "Extract agents, prompts, and the output leak guard"
```

---

## Task 5: Graphs

**Files:**
- Create: `app/graphs/__init__.py`, `specialist.py`, `planner.py`
- Test: `tests/test_graphs.py`

**Interfaces:**
- Produces: `specialist.CriticState`, `specialist.supervisor_critic_graph` (compiled, no checkpointer — it is a one-shot helper per subtask); `planner.PlannerState`, `planner.build_graph(checkpointer) -> CompiledStateGraph`, and the node functions `plan`, `human_approval`, `research_subtask`, `synthesize`, `has_more_subtasks`, `route_after_approval`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graphs.py
from app.graphs.planner import has_more_subtasks
from app.graphs.specialist import supervisor_critic_graph


def test_specialist_graph_topology_is_unchanged():
    nodes = set(supervisor_critic_graph.get_graph().nodes)
    assert {
        "supervisor",
        "research_agent",
        "knowledge_agent",
        "analysis_agent",
        "critic",
    } <= nodes


def test_subtask_loop_advances_and_terminates():
    assert has_more_subtasks({"subtasks": ["a", "b"], "current_index": 0}) == "research_subtask"
    assert has_more_subtasks({"subtasks": ["a", "b"], "current_index": 2}) == "synthesize"


def test_empty_plan_goes_straight_to_synthesize():
    assert has_more_subtasks({"subtasks": [], "current_index": 0}) == "synthesize"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_graphs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.graphs'`

- [ ] **Step 3: Write the implementation**

Move lines 403-600 to `specialist.py` and 607-753 to `planner.py`. One structural change only: the original compiled the outer graph at module scope (`graph = graph_builder.compile(checkpointer=checkpointer)`, line 753). Wrap that in `build_graph(checkpointer)` so importing `planner` needs no database. The builder wiring is otherwise unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_graphs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/graphs tests/test_graphs.py
git commit -m "Extract the specialist and planner graphs"
```

---

## Task 6: Ingestion and security modules

**Files:**
- Create: `app/ingestion/__init__.py`, `extract.py`, `store.py`
- Create: `app/security/validation.py`, `locks.py`, `ratelimit.py`
- Test: `tests/test_ingestion.py`, `tests/test_security.py`

**Interfaces:**
- Produces: `extract.get_file_type(filename) -> str | None`; `extract.extract_text(file_bytes, file_type) -> str`; `extract.extract_text_with_timeout(file_bytes, file_type) -> str`; `store.chunk_text(text) -> list[str]`; `store.embed_and_store(conn, document_id, chunks) -> int`; `validation.validate_text_field(value, field_name, max_length=None) -> str`; `locks.thread_lock(thread_id)` (contextmanager); `ratelimit.enforce_rate_limits(scope, user_id, client_ip, user_limit, ip_limit) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_security.py
import pytest
from fastapi import HTTPException

from app.security.ratelimit import _rate_limit_state, enforce_rate_limits
from app.security.validation import validate_text_field


def test_each_scope_has_an_independent_budget():
    _rate_limit_state.clear()
    for _ in range(10):
        enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    assert exc.value.status_code == 429
    # a different scope must NOT be exhausted by chat traffic
    enforce_rate_limits("search", "u1", "1.1.1.1", (20, 60), (60, 60))


def test_blank_text_field_is_rejected():
    with pytest.raises(HTTPException):
        validate_text_field("   ", "question")
```

```python
# tests/test_ingestion.py
from app.ingestion.extract import get_file_type


def test_recognises_supported_types():
    assert get_file_type("report.PDF") == "pdf"
    assert get_file_type("notes.txt") == "txt"
    assert get_file_type("archive.zip") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_security.py tests/test_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Move lines 829-927 to `ingestion/extract.py`; the chunk/embed/persist body currently inline in `upload_document` (roughly lines 1520-1580) to `ingestion/store.py`; lines 910-927 to `security/validation.py`; 967-1003 to `security/locks.py`; 1035-1082 to `security/ratelimit.py`.

Drop the leading underscore on the three names that now cross a module boundary (`_validate_text_field` -> `validate_text_field`, `_thread_lock` -> `thread_lock`, `_enforce_rate_limits` -> `enforce_rate_limits`). Keep `_enforce_rate_limit` and `_lock_for` private. Keep `_rate_limit_state` and `_thread_locks` at module scope so tests can clear them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_security.py tests/test_ingestion.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion app/security tests/test_ingestion.py tests/test_security.py
git commit -m "Extract ingestion pipeline and security helpers"
```

---

## Task 7: API layer

**Files:**
- Create: `app/api/__init__.py`, `schemas.py`, `middleware.py`, `factory.py`, `routers/{__init__,health,chat,documents}.py`
- Create: `app/main.py`
- Test: `tests/test_schema_parity.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `app.api.factory.create_app() -> FastAPI`; `app.main.app`.
- `create_app()` calls `init_schema()` and `build_graph(get_checkpointer())` from a lifespan handler, NOT at import.

- [ ] **Step 1: Write the failing test — this is the one that proves the port**

```python
# tests/test_schema_parity.py
"""The port is faithful iff the new app exposes exactly the old app's HTTP
contract. Compares the two OpenAPI schemas route-by-route and model-by-model."""
import importlib.util
import sys
from pathlib import Path

import pytest

OLD = Path("stage25_react_ui/backend/main.py")


def _load_original():
    sys.path.insert(0, str(OLD.parent))
    spec = importlib.util.spec_from_file_location("stage25_main", OLD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


@pytest.fixture(scope="module")
def schemas(pg_available):
    from app.main import app as new_app

    return _load_original().openapi(), new_app.openapi()


def test_same_routes_and_methods(schemas):
    old, new = schemas
    old_routes = {(p, m) for p, ops in old["paths"].items() for m in ops}
    new_routes = {(p, m) for p, ops in new["paths"].items() for m in ops}
    assert new_routes == old_routes


def test_same_response_models(schemas):
    old, new = schemas
    assert new["components"]["schemas"] == old["components"]["schemas"]
```

Note: `_load_original()` needs Postgres because the original connects at import — hence the `pg_available` fixture. That asymmetry is itself the point of the refactor.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schema_parity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write the implementation**

Move the 13 pydantic models (lines 1093-1202) to `schemas.py` verbatim — field names, `Literal` values, defaults, and docstrings unchanged, or `test_same_response_models` fails. Move lines 1233-1263 to `middleware.py`. `factory.py` builds `FastAPI(...)` with the **same `title` and `description` strings** and the same `CORSMiddleware` arguments. Each router keeps its routes' `response_model=` and path exactly.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schema_parity.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/api app/main.py tests/test_schema_parity.py
git commit -m "Add API layer with OpenAPI schema parity against stage 25"
```

---

## Task 8: End-to-end verification against the real stack

**Files:**
- Create: `tests/conftest.py`, `tests/test_app_backend.py`

**Prerequisites:** `docker compose up -d` (pgvector on 5433) and `OPENAI_API_KEY` set. This task calls the real OpenAI API and costs money — intentional, and it matches every existing stage's test convention.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
import os

import pytest


@pytest.fixture(scope="session")
def pg_available():
    from app.db import get_connection

    try:
        get_connection().execute("SELECT 1").fetchone()
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")


@pytest.fixture(scope="session")
def openai_available():
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
```

- [ ] **Step 2: Port the Stage 25 suite**

Copy `stage25_react_ui/backend/test_react_ui_backend.py` to `tests/test_app_backend.py`, converting its `assert`+`print` script style into pytest functions. Change **only** the imports (`from main import app, pg_conn` -> `from app.main import app` / `from app.db import get_connection`) and the function wrappers. Every assertion keeps its original expected value.

- [ ] **Step 3: Run the ported suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_backend.py -v`
Expected: PASS — every test that passed for stage 25 passes here.

- [ ] **Step 4: Run the Stage 24 regression against the new app**

The original test module has `run_stage24_regression()`, which re-runs `stage24_security_guardrails/test_security_guardrails.py` against stage 25's `app`. Point that same suite at `app.main.app` and run it.

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: PASS, full suite.

- [ ] **Step 5: Manual smoke against the real frontend**

The API surface is identical, so the existing React app needs no change:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

Confirm in the browser: upload a document, ask a question, approve, watch the trace populate. Then confirm the frozen stage is untouched: `git status --short stage25_react_ui/` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "Port stage 25 and stage 24 test suites onto the app package"
```

---

## Definition of Done

- [ ] `tests/test_schema_parity.py` passes — the HTTP contract is provably identical.
- [ ] `tests/test_app_backend.py` passes against real Postgres + real OpenAI.
- [ ] `git status --short stage25_react_ui/` is empty — the frozen stage is untouched.
- [ ] `.venv/Scripts/python.exe -c "import app.main"` succeeds with Postgres **stopped** (import-time safety).
- [ ] No file in `app/` exceeds ~250 lines.
- [ ] The known bugs (shared connection, unbounded `_thread_locks` / `_rate_limit_state`, hardcoded `status: "completed"`) are still present, each carrying a comment naming the phase that fixes it.
