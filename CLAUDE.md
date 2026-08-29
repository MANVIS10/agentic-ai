# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A "Personal Research Assistant" built on LangGraph/LangChain, in two halves:
a **running application** (`app/` + `frontend/`) and the **25-stage learning
archive** it grew out of (`stages/`). The stages take the assistant from a
plain chatbot up to a deployed multi-agent system, one concept at a time.
The user is a beginner learning the frameworks; code should stay readable and
pedagogical over clever or terse.

## Repo layout

```
app/        FastAPI + LangGraph backend (the production package)
frontend/   React + TypeScript UI
stages/     The 25 learning stages, each self-contained and runnable
tests/      pytest suite for app/
docs/       Specs, plans, and the project log (docs/PROGRESS.md)
```

## Setup and running

```bash
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

# a stage
python stages/stage1_chatbot/main.py

# the app (needs Postgres + pgvector from docker-compose.yml, port 5433)
docker compose up -d
uvicorn app.main:app --port 8000
```

`OPENAI_API_KEY` is read from `.env` at the repo root via `python-dotenv`.
There is no linter configured. The only pytest suite is `tests/`, which
covers `app/` (see below); the stage folders' own tests (e.g.
`stages/stage3_rag/test_search_knowledge_base.py`) are standalone scripts run
directly with `python` — asserts + prints, no test framework dependency.

Deployment is documented in `stages/stage25_react_ui/DEPLOYMENT.md` (Neon
Postgres -> Render backend -> Vercel frontend); there is no numbered
"deployment stage".

## `app/` package tests (production port)

`app/` is a production-style port of `stages/stage25_react_ui/backend/main.py`
(see `docs/superpowers/plans/2026-08-25-production-package-port.md`),
tested with pytest under `tests/`. It has since grown behavior the stages
don't have — most visibly a conversational branch in front of the planner
(`classify` -> `greet`, so "I'm Manvi" is answered directly instead of
being planned and sent to the approval gate). These are additive: the HTTP
contract is unchanged, which `tests/test_schema_parity.py` enforces. The unit tests there must **not** call the
real OpenAI API — no `.invoke()`/`.embed_*()` on a live
`ChatOpenAI`/`OpenAIEmbeddings` instance during a normal `pytest tests/` run,
since that costs money and makes the suite non-deterministic and
network-dependent. Only end-to-end tests explicitly gated behind the
`openai_available` fixture (skipped whenever `OPENAI_API_KEY` isn't set) may
hit the real API — same spirit as this project's existing convention of
calling out real-API-cost tests explicitly rather than running them by
default. In practice that gate is `tests/test_app_backend.py`, so the
network-free run is:

```bash
pytest tests/ --ignore=tests/test_app_backend.py
```

## Architecture: stage folders

Each stage lives in its own folder under `stages/` and is a
**self-contained, runnable script** — not a shared library. Later stages are
expected to duplicate setup code from earlier ones (LLM init, graph
boilerplate) rather than import a common module. This is deliberate: each
folder should be readable top-to-bottom on its own so the user can diff one
stage against the next to see exactly what concept was added. Do not refactor
shared code out into a `common/` module unless explicitly asked. (`app/` is
the one exception, and it is a *port* of Stage 25 rather than a dependency of
any stage — the stages stay frozen and runnable on their own.)

Every stage folder must include its own `README.md` covering: what was
added, which LangChain/LangGraph concept it demonstrates, its architecture,
how to run it, and what changed compared with the previous stage. A stage
isn't done until this README exists.

Implement only one stage at a time — do not build ahead into future-stage
functionality unless explicitly requested. When adding a new stage, leave
previous stages' code untouched (README/roadmap status updates in the
top-level `README.md` and `docs/PROGRESS.md` excepted).

## The 25 stages

All 25 are implemented. The top-level `README.md` has the full table and
`docs/PROGRESS.md` the running log; each folder's own `README.md` has the
detail. Summary:

1. `stage1_chatbot/` — `StateGraph` + `MemorySaver`, single node, terminal REPL
   with per-thread memory.
2. `stage2_tool_agent/` — tool calling / ReAct loop via `bind_tools`,
   `ToolNode`, `tools_condition`, using `DuckDuckGoSearchRun`.
3. `stage3_rag/` — retrieval over a local markdown knowledge base:
   `RecursiveCharacterTextSplitter`, `OpenAIEmbeddings`, `InMemoryVectorStore`,
   exposed as one `search_knowledge_base` tool. Node renamed `chatbot` -> `agent`.
4. `stage4_web_fetch/` — `fetch_webpage`: HTTP GET + BeautifulSoup
   HTML-to-text. A tool with a real external side effect rather than an index
   read.
5. `stage5_pdf_fetch/` — `fetch_pdf`: `requests` for raw bytes, `pypdf` for
   page-by-page text. No `Content-Type` sniffing, no HTML fallback.
6. `stage6_planner/` — custom state schema + a hand-written conditional edge
   (no `bind_tools`): breaks a question into 2-3 subtasks, loops over them one
   at a time, synthesizes a final answer.
7. `stage7_human_in_loop/` — Stage 6 plus a `human_approval` node calling
   `interrupt()` once to show the plan and wait for y/n, resumed via
   `Command(resume=...)`; rejection routes straight to `END`.
8. `stage8_research_workflow/` — Stage 7's planner unchanged, but each subtask
   is researched by a tool-calling agent with all four earlier tools bound
   together. Composing a compiled graph as a callable inside another node.
9. `stage9_simple_memory/` — Stage 1's chatbot plus `remember: <text>` /
   `recall` backed by a JSON file. Long-term memory vs. per-thread graph state.
10. `stage10_multi_tool_agent/` — Stage 2's flat loop with all four tools bound
    so the LLM picks whichever fits. Tool selection, isolated from planning.
11. `stage11_research_agent/` — Stage 2 narrowed to one tool (`search_web`) plus
    a system prompt naming it a "Research Agent". Specialization vs. generalist.
12. `stage12_two_specialist_agents/` — Research + Knowledge specialists side by
    side, zero shared state, picked by a hard-coded prefix.
13. `stage13_supervisor/` — those two specialists as subgraphs inside one outer
    graph, with a supervisor node routing between them (structured LLM output
    + conditional edge on a routing field).
14. `stage14_critic/` — a critic node reviews the answer (pass/retry) and can
    send one bounded retry back to the same specialist with feedback attached.
15. `stage15_analysis_agent/` — a third standalone specialist with one tool,
    `calculate` (safe `ast`-based arithmetic). A "compute" specialist rather
    than a "retrieve" one; no supervisor wiring yet.
16. `stage16_three_specialist_supervisor/` — the Analysis Agent joins Stage
    13/14's graph: a wider routing `Literal` and one more dispatch entry. The
    critic needed zero changes.
17. `stage17_final_multi_agent_system/` — Stage 7/8's planner + approval loop
    with each subtask delegated to Stage 16's full supervisor +
    three-specialist + critic pipeline. Pure composition of two
    independently-built graphs; the original roadmap's capstone.

Stages 18-25 are deliberate extensions added *after* that roadmap closed at
Stage 17 — each takes the previous stage's app essentially unchanged and adds
a single production concern:

18. `stage18_postgres_persistence/` — `MemorySaver` -> `PostgresSaver`
    (Postgres via the root `docker-compose.yml`). A paused conversation now
    survives a process restart.
19. `stage19_fastapi_backend/` — the same graph behind FastAPI (`/health`,
    `/chat`, `/approve`, `/reject`) instead of a REPL, so
    `interrupt()`/`Command(resume=...)` spans two HTTP requests and
    `graph.get_state()` becomes load-bearing.
20. `stage20_document_upload/` — `POST /documents/upload`: validate, extract,
    chunk, and store a PDF/TXT/DOCX in two hand-written Postgres tables
    (`documents`, `document_chunks`). Storage only, no embeddings.
21. `stage21_semantic_search/` — an `embedding vector(1536)` column
    (`pgvector`, image swapped to `pgvector/pgvector:pg16`), a backfill
    endpoint, and `POST /documents/search` (cosine similarity via `<=>`).
    The repo's first schema *evolution*. Search only, no agent wiring.
22. `stage22_knowledge_agent_rag/` — the Knowledge Agent's tool *replaced*:
    `search_uploaded_documents` (pgvector over `document_chunks`) instead of
    `search_knowledge_base`. Supervisor/critic/planner untouched — a
    specialist's tool can be swapped from underneath them. Deliberately a
    replacement, so the bundled `knowledge_base/*.md` is unreachable here; it
    stays intact in the earlier stages only.
23. `stage23_user_document_isolation/` — every document owned by a `user_id`,
    filtered on both retrieval paths. The tool's `user_id` arrives via
    `langgraph.prebuilt.InjectedState` from graph state, invisible to the
    model, so the LLM can never be tricked into supplying someone else's.
24. `stage24_security_guardrails/` — hardening, no new capability: bounded
    reads, PDF-page/DOCX-zip-bomb/extraction-timeout caps collapsing into one
    generic `422`, input length limits, a body-size middleware, an
    untrusted-content envelope + hardened prompt around retrieved document
    text, a deterministic (non-LLM) system-prompt-leak check, and in-process
    per-route rate limiting.
25. `stage25_react_ui/` — a React + TypeScript SPA (upload/list, chat,
    approval, execution trace) against Stage 24's exact contract, plus two
    additive backend changes in `stages/stage25_react_ui/backend/main.py`:
    `GET /documents` and a `trace` field on `ThreadStatusResponse`. No node,
    edge, prompt, or tool changes. `app/` is the production port of this.

Folder numbers drifted from the original roadmap: the web-fetch and PDF-fetch
tools (4-5) were built by request ahead of planning, so planning landed at 6
and human-in-the-loop at 7; likewise stages 12-14 sit one ahead of the project
spec's own numbering (spec "Stage 11 — Specialist Agents" ->
`stage12_two_specialist_agents`, and so on). Trust the folder names and
`docs/PROGRESS.md`, not the spec numbering.

Tools do not accumulate across stages — each stage binds only what it needs:
Stage 2 -> `search_web`, Stage 3 -> `search_knowledge_base`, Stage 4 ->
`fetch_webpage`, Stage 5 -> `fetch_pdf`, Stages 8 and 10 -> all four bound
together, Stages 11-14 and 16-21 -> one tool per specialist (`search_web` /
`search_knowledge_base`, plus `calculate` from Stage 15), Stages 22-25 ->
`search_uploaded_documents` in place of `search_knowledge_base`. Stages 6, 7,
9, 18, and 19 add no tool at all.

## Stage 1 pattern (reused going forward)

Every stage builds a graph the same way: define a state schema, add node
functions that take state and return a partial update, wire nodes with
`add_edge`/`START`/`END`, then `.compile()`. Conversation memory is
per-`thread_id`, passed via `config={"configurable": {"thread_id": ...}}` on
`graph.invoke()` — the checkpointer merges saved history automatically, so
callers only ever pass the new message in, not the full history.
