# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A project-based learning repo for LangGraph/LangChain, structured as a "Personal
Research Assistant" that grows in capability stage by stage — from a plain
chatbot up to a multi-agent system. The user is a beginner learning the
frameworks; code should stay readable and pedagogical over clever or terse.

## Setup and running

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage1_chatbot/main.py
```

`OPENAI_API_KEY` is read from `.env` via `python-dotenv`. There is no build
step, linter, or test suite configured yet. Where a stage has a test (e.g.
`stage3_rag/test_search_knowledge_base.py`), it's a standalone script run
directly with `python`, not a pytest suite — asserts + prints, no test
framework dependency.

## `app/` package tests (production port)

`app/` is a production-style port of `stage25_react_ui/backend/main.py`
(see `docs/superpowers/plans/2026-08-25-production-package-port.md`),
tested with pytest under `tests/`. Unit tests there (`test_config.py`,
`test_db.py`, `test_tools.py`, `test_agents.py`, `test_graphs.py`,
`test_ingestion.py`, `test_security.py`) must **not** call the real OpenAI
API — no `.invoke()`/`.embed_*()` on a live `ChatOpenAI`/`OpenAIEmbeddings`
instance during a normal `pytest tests/` run, since that costs money and
makes the suite non-deterministic and network-dependent. Only an
end-to-end test explicitly gated behind the `openai_available` fixture
(skipped whenever `OPENAI_API_KEY` isn't set) may hit the real API — same
spirit as this project's existing convention of calling out real-API-cost
tests explicitly rather than running them by default.

## Architecture: stage folders

Each stage lives in its own top-level folder (`stage1_chatbot/`,
`stage2_tool_agent/`, ...) and is a **self-contained, runnable script** — not
a shared library. Later stages are expected to duplicate setup code from
earlier ones (LLM init, graph boilerplate) rather than import a common
module. This is deliberate: each folder should be readable top-to-bottom on
its own so the user can diff one stage against the next to see exactly what
concept was added. Do not refactor shared code out into a `common/` module
unless explicitly asked.

Every stage folder must include its own `README.md` covering: what was
added, which LangChain/LangGraph concept it demonstrates, its architecture,
how to run it, and what changed compared with the previous stage. A stage
isn't done until this README exists.

Implement only one stage at a time — do not build ahead into future-stage
functionality unless explicitly requested. When adding a new stage, leave
previous stages' code untouched (README/roadmap status updates in the
top-level `README.md` excepted).

The progression so far (see `README.md` and `PROGRESS.md` for the full
table and status checklist):

1. `stage1_chatbot/` — `StateGraph` + `MemorySaver`, single node, terminal
   REPL with per-thread memory (implemented)
2. `stage2_tool_agent/` — tool calling / ReAct loop via `bind_tools`,
   `ToolNode`, and `tools_condition`, using `DuckDuckGoSearchRun` (no API
   key needed) (implemented)
3. `stage3_rag/` — retrieval over a local markdown knowledge base:
   `RecursiveCharacterTextSplitter` for chunking, `OpenAIEmbeddings`, and
   `InMemoryVectorStore` (no extra vector-store dependency), exposed as a
   single `search_knowledge_base` tool. Same `agent -> tools -> agent`
   conditional-edge shape as Stage 2 (node renamed `chatbot` -> `agent`)
   (implemented)
4. `stage4_web_fetch/` — `fetch_webpage` tool: fetches a URL over HTTP and
   parses its HTML into text with BeautifulSoup, a tool with a real
   external side effect rather than just reading from an index
   (implemented)
5. `stage5_pdf_fetch/` — `fetch_pdf` tool: downloads a PDF's raw bytes with
   `requests` and extracts its text page-by-page with `pypdf`, no
   `Content-Type` sniffing or HTML fallback (implemented)
6. `stage6_planner/` — custom state schema + a hand-written conditional
   edge (no `bind_tools`) that breaks a research question into 2-3
   subtasks, loops over them one at a time, and synthesizes a final answer
   (implemented)
7. `stage7_human_in_loop/` — Stage 6's planner with one added node,
   `human_approval`, that calls `interrupt()` once to show the plan and
   wait for human y/n approval before research begins, resumed via
   `Command(resume=...)`; rejection routes straight to `END` with no
   research done (implemented)

Stages 4-7 deviated from the original planned slots below them: the
web-fetch and PDF-fetch tools were built by request ahead of planning, the
planning concept (originally slated for stage 4) ended up at stage 6, and
human-in-the-loop (originally slated for stage 5) ended up at stage 7. One
concept from the original roadmap is still unbuilt and doesn't have a
folder number assigned yet:

- Multi-agent system — planner/researcher/writer/reviewer as separate
  collaborating agents (subgraphs)

Each tool-using stage binds exactly one tool to its agent (stages don't
accumulate each other's tools): Stage 2 -> `DuckDuckGoSearchRun` (web
search), Stage 3 -> `search_knowledge_base` (local retrieval), Stage 4 ->
`fetch_webpage` (HTTP fetch + HTML parsing), Stage 5 -> `fetch_pdf`
(PDF download + text extraction). Stages 6 and 7 use plain LLM calls with
no bound tool.

## Stage 1 pattern (reused going forward)

Every stage builds a graph the same way: define a state schema, add node
functions that take state and return a partial update, wire nodes with
`add_edge`/`START`/`END`, then `.compile()`. Conversation memory is
per-`thread_id`, passed via `config={"configurable": {"thread_id": ...}}` on
`graph.invoke()` — the checkpointer merges saved history automatically, so
callers only ever pass the new message in, not the full history.
