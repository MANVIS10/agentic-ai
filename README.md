# Personal Research Assistant — Learning Roadmap

One project, growing stage by stage from a plain chatbot into a multi-agent
research system. Each stage lives in its own folder so you can look back at
how the code evolved. Concepts build on each other — don't skip ahead.

## Stages

| Stage | Folder | What it does | New concept |
|---|---|---|---|
| 1 | `stage1_chatbot/` | Terminal chatbot that remembers the conversation | `StateGraph`, nodes/edges, checkpointer memory |
| 2 | `stage2_tool_agent/` | Chatbot can search the web to answer questions | Tool calling, ReAct loop |
| 3 | `stage3_rag/` | Answers questions grounded in your own documents | Embeddings, vector store, retrieval |
| 4 | `stage4_web_fetch/` | Fetches a URL and reads its page text | Tool with a real HTTP side effect |
| 5 | `stage5_pdf_fetch/` | Downloads a PDF and reads its extracted text | Binary content, PDF text extraction |
| 6 | `stage6_planner/` | Breaks a research question into subtasks, researches each, combines results | Custom state schema, hand-written conditional-edge loop |
| 7 | `stage7_human_in_loop/` | Shows the research plan and pauses for human y/n approval before any research runs | `interrupt()` / `Command(resume=...)`, pausing and resuming a graph |
| 8 | `stage8_research_workflow/` | Reuses Stage 7's plan/approval loop, but each subtask is now researched by a tool-calling agent with all four earlier tools bound together | Composing a compiled graph as a callable inside another graph's node |
| 9 | `stage9_simple_memory/` | Stage 1's chatbot plus `remember: <text>` / `recall` backed by a JSON file on disk | Long-term memory vs. per-thread graph state |
| 10 | `stage10_multi_tool_agent/` | Stage 2's flat chat loop, but with all four tools from Stages 2-5 bound together so the LLM picks whichever fits | Tool selection, isolated from planning/composition |
| 11 | `stage11_research_agent/` | Stage 2's agent narrowed to one tool (web search) plus a system prompt naming it a "Research Agent" | Specialization - a declared role + narrow toolset, vs. a generalist |
| 12 | `stage12_two_specialist_agents/` | Two independent specialists (Research Agent, Knowledge Agent), Stage 11's pattern repeated with different tools, picked by a hard-coded prefix | Proving specialization generalizes - two agents coexisting with zero shared state |
| 13 | `stage13_supervisor/` | Same two specialists, now as subgraphs inside one outer graph with a supervisor node routing between them | Structured LLM output + conditional edge on a routing field |
| 14 | `stage14_critic/` | Same supervisor + specialists, plus a critic node that reviews the answer and can send one bounded retry back to the same specialist | A second structured-output node judging quality, plus a bounded retry loop (conditional edge routing backward) |
| 15 | `stage15_analysis_agent/` | A third independent specialist (Stage 11/12's pattern again) with one tool, `calculate`, for arithmetic over numbers given in the conversation | A "compute" specialist rather than a "retrieve" one - same graph shape, different kind of tool |
| 16 | `stage16_three_specialist_supervisor/` | Stage 15's Analysis Agent joins Stage 13/14's supervisor + critic graph, alongside Research and Knowledge | Widening a structured-output routing field from two choices to N, and confirming the critic needs no changes to support it |
| 17 | `stage17_final_multi_agent_system/` | Stage 7/8's planner + human-approval loop, with each subtask now researched by Stage 16's full supervisor + three-specialist + critic pipeline instead of a plain LLM call or a flat tool agent | The final combined multi-agent research assistant - composing two independently-built graphs, proving a compiled `StateGraph` invoked inside a node is just a function call regardless of how elaborate that graph is |
| 18 | `stage18_postgres_persistence/` | Stage 17's exact graph, with its checkpointer swapped from `MemorySaver` to `PostgresSaver` (Postgres via Docker Compose) | Durable checkpointing - a paused or completed conversation now survives a Python process restart, not just a `thread_id` switch within the same process |
| 19 | `stage19_fastapi_backend/` | Stage 18's exact graph, wrapped in a FastAPI HTTP API (`/health`, `/chat`, `/approve`, `/reject`) instead of a terminal REPL, same Postgres checkpointer | Exposing a compiled LangGraph graph over HTTP - `interrupt()`/`Command(resume=...)` now spans two separate HTTP requests instead of one blocking REPL loop, and `graph.get_state()` becomes the way to validate a pending approval before resuming it |
| 20 | `stage20_document_upload/` | Stage 19's exact app plus one new endpoint, `POST /documents/upload`, that validates, extracts, chunks, and durably stores an uploaded PDF/TXT/DOCX file in two new Postgres tables (`documents`, `document_chunks`) | Accepting and storing arbitrary user-supplied file uploads - the first hand-written (non-checkpointer-owned) Postgres tables in this repo; storage only, no embeddings/retrieval yet |
| 21 | `stage21_semantic_search/` | Stage 20's exact app plus embeddings for every uploaded chunk (`pgvector`), a backfill endpoint for pre-existing chunks, and `POST /documents/search` for cosine-similarity search with configurable `top_k`/threshold/document scoping | Durable vector storage via Postgres + `pgvector`, instead of an in-memory store rebuilt every process start; the first schema *evolution* (`ALTER TABLE ... ADD COLUMN`) in this repo, not just first-time table creation; search only, no RAG/agent wiring yet |
| 22 | `stage22_knowledge_agent_rag/` | Stage 21's exact app with the Knowledge Agent's tool replaced: `search_uploaded_documents` (pgvector search over `document_chunks`, in-process) instead of `search_knowledge_base` (the bundled `knowledge_base/*.md`) | A specialist's tool can be swapped out entirely without touching the supervisor, critic, or planner above it - `knowledge_node` only ever calls `knowledge_graph.invoke(...)` and never references a tool by name. Deliberately a *replacement*, not an addition: the bundled knowledge base is unreachable from this stage for normal queries, kept intact only in Stage 3-21 for historical compatibility |

`stage4_web_fetch`, `stage5_pdf_fetch`, `stage6_planner`, and
`stage7_human_in_loop` are follow-on tool stages built by request rather
than the original numbered slots below (`stage4_planner` was the original
name for what's now `stage6_planner`; `stage5_human_in_loop` was the
original name for what's now `stage7_human_in_loop`). Stages 12-14 follow
the same pattern against the project spec's own numbering (spec "Stage 11 —
Specialist Agents" -> `stage12_two_specialist_agents`, spec "Stage 12 —
Supervisor" -> `stage13_supervisor`, spec "Stage 13 — Critic" ->
`stage14_critic`) — see `PROGRESS.md` for the up-to-date picture.

Each folder has its own `README.md` with the full breakdown: what was
added, the concept it demonstrates, its architecture, how to run it, and
what changed vs. the previous stage.

The long-term target concept progression is more granular than the folders
above, and not yet fully reconciled with the numbering that actually
happened on disk: stateful chatbot -> tools -> web research -> ReAct agent
-> RAG -> memory -> planning -> specialist agents -> supervisor -> critic ->
multi-agent research system. Rather than one folder per concept, several
adjacent concepts are taught together within a single stage folder:

- Stage 2 covers tools + web research + the ReAct agent loop together.
- Stage 3 covers RAG plus document-grounded memory.
- Stages 4-5 cover tool side effects beyond retrieval (HTTP fetch, binary
  PDF content) rather than planning — a deviation from the original slot.
- Stage 6 covers planning (breaking a question into subtasks via a
  hand-written conditional-edge loop) — this was originally meant to be
  Stage 4.
- Stage 7 covers human-in-the-loop approval — it extends Stage 6's planner
  with one `interrupt()` before research begins, pausing the graph for a
  human to approve or reject the whole plan, then `Command(resume=...)`
  continuing it (or routing straight to `END` on rejection) — this was
  originally meant to be Stage 5.
- Stage 8 covers combining existing capabilities — Stage 7's planner is
  unchanged, but each subtask is now researched by a small tool-calling
  agent with all four tools from Stages 2-5 bound together, so the model
  picks whichever tool actually fits each subtask.
- Stage 9 covers long-term memory — Stage 1's one-node chatbot is
  unchanged, with `save_memory`/`load_memory` (plain functions, not tools)
  added alongside it to show a fact on disk outlives `MemorySaver`'s
  per-thread state, surviving a different `thread_id` or a process
  restart.
- Stage 10 covers tool selection on its own - the same four tools Stage 8
  bound together, but as the whole graph (Stage 2's flat `agent -> tools ->
  agent` loop) instead of one node inside a bigger planner graph.
- Stage 11 covers specialization - Stage 2's loop narrowed to one tool
  (web search) plus a system prompt declaring the agent's identity and
  job, in contrast to Stage 10's generalist multi-tool agent.
- Stage 12 covers specialist agents plural - Stage 11's pattern (narrow
  toolset + declared identity) stamped out twice with different tools, run
  side by side in one process with zero shared state or communication, and
  picked by a hard-coded prefix typed by the human.
- Stage 13 covers a supervisor - a routing node (structured LLM output)
  placed in front of Stage 12's two specialists (now subgraphs inside one
  outer graph), replacing the hard-coded prefix with an actual classify-
  and-route decision.
- Stage 14 covers a critic - a review node placed after Stage 13's
  specialists that judges the answer (structured LLM output: pass/retry)
  and can send one bounded retry back to the same specialist with
  feedback attached, before the answer is treated as final.
- Stage 15 covers the third named specialist from the spec (Research,
  Knowledge, Analysis) - Stage 11/12's pattern (narrow toolset + declared
  identity) stamped out a third time, with one tool (`calculate`, a safe
  `ast`-based arithmetic evaluator) for sums, averages, percentage change,
  and comparisons over numbers given directly in the conversation. Built
  standalone with no supervisor/critic wiring, same as Stage 11/12 were
  before Stage 13 added routing.
- Stage 16 covers widening the supervisor + critic to a third specialist -
  Stage 15's Analysis Agent plugs into Stage 13/14's graph with only a
  wider routing `Literal` and one more entry in each conditional-edge
  dispatch dict. The critic (Stage 14) needed zero code changes, since it
  only ever judges a question/answer pair and never special-cases which
  specialist produced it.
- Stage 17 covers the final combined multi-agent system - Stage 7/8's
  planner + human-approval loop, with `research_subtask` now delegating
  each subtask to Stage 16's full supervisor + three-specialist + critic
  pipeline instead of a plain LLM call (Stage 6/7) or a single flat 4-tool
  agent (Stage 8). Nothing new was invented: it's pure composition of two
  independently-built graphs, meeting only at one function call inside
  `research_subtask`.
- Stage 18 covers durable checkpointing - a deliberate extension added
  after the roadmap above closed at Stage 17, not a missed roadmap item.
  Stage 17's exact graph, unchanged, with `MemorySaver` swapped for
  `PostgresSaver` (a real Postgres database, provisioned via the root
  `docker-compose.yml`), so a paused-for-approval or completed
  conversation now survives killing and restarting the Python process.
- Stage 19 covers exposing the graph over HTTP - another deliberate
  extension past the closed roadmap. Stage 18's exact graph, unchanged,
  wrapped in a FastAPI app with four endpoints instead of a terminal REPL.
  `interrupt()`/`Command(resume=...)` now spans two separate HTTP requests
  (`POST /chat` pauses and returns; a later `POST /approve` or
  `POST /reject` resumes the same `thread_id`) instead of one blocking
  `input()` loop, and `graph.get_state()` becomes load-bearing rather than
  a debugging convenience - it's how `/approve`/`/reject` confirm a thread
  is actually paused before resuming it.
- Stage 20 covers document upload and ingestion - another deliberate
  extension past the closed roadmap. Stage 19's exact app, unchanged, plus
  `POST /documents/upload`: validates a PDF/TXT/DOCX file, extracts its
  text (`pypdf`/`python-docx`/plain decode), chunks it
  (`RecursiveCharacterTextSplitter`, same settings as the bundled
  knowledge-base loader), and stores it in two new tables written with
  hand-written SQL rather than owned by `PostgresSaver`. Deliberately
  storage-only - no embeddings, vector search, or Knowledge Agent wiring
  yet (see `.claude/spec/stage20_document_upload_spec.md`).
- Stage 21 covers embeddings and semantic vector search - another
  deliberate extension past the closed roadmap. Stage 20's exact app,
  unchanged, plus an `embedding vector(1536)` column added to
  `document_chunks` (the first schema *evolution* in this repo, via
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, not just first-time table
  creation), a `POST /documents/backfill-embeddings` endpoint for chunks
  uploaded before this stage existed, and `POST /documents/search` for
  cosine-similarity search (`pgvector`'s `<=>` operator) with configurable
  `top_k`, an optional similarity threshold, and optional per-document
  scoping. Requires `docker-compose.yml`'s Postgres image swapped to
  `pgvector/pgvector:pg16` to support the extension. Deliberately search-
  only - no RAG answer generation or Knowledge Agent wiring yet (see
  `.claude/spec/stage21_semantic_search_spec.md`).
- Stage 22 covers wiring that search into the Knowledge Agent - another
  deliberate extension past the closed roadmap. Stage 21's exact app,
  unchanged, except the Knowledge Agent's tool is *replaced*:
  `search_uploaded_documents` (a new `@tool` running Stage 21's cosine-
  similarity query in-process against `document_chunks`) instead of
  `search_knowledge_base` (the bundled `knowledge_base/*.md` via
  `InMemoryVectorStore`). This is a deliberate design choice, not the
  default additive one: for normal queries, the bundled knowledge base
  must not be reachable at all, so it's dropped from this stage's own copy
  entirely rather than bound alongside the new tool. It remains fully
  intact and working in Stage 3, 8, 10, 16-21 - "historical compatibility"
  means those folders are untouched, not that this stage carries the
  capability forward. The supervisor, critic, planner, and every other
  route are byte-identical to Stage 21 (see
  `.claude/spec/stage22_knowledge_agent_rag_spec.md`).

## Setup

 virtual environment— `.venv` is used below
— and consider deleting the other once you've confirmed which you're using.

```
.venv\Scripts\activate
pip install -r requirements.txt
```

`OPENAI_API_KEY` is already set in `.env`.

## Running a stage

```
python stage1_chatbot/main.py
```

## Status

- [x] Stage 1 — scaffolded
- [x] Stage 2
- [x] Stage 3
- [x] Stage 4 (`stage4_web_fetch`)
- [x] Stage 5 (`stage5_pdf_fetch`)
- [x] Stage 6 (`stage6_planner`)
- [x] Stage 7 (`stage7_human_in_loop`)
- [x] Stage 8 (`stage8_research_workflow`)
- [x] Stage 9 (`stage9_simple_memory`)
- [x] Stage 10 (`stage10_multi_tool_agent`)
- [x] Stage 11 (`stage11_research_agent`)
- [x] Stage 12 (`stage12_two_specialist_agents`)
- [x] Stage 13 (`stage13_supervisor`)
- [x] Stage 14 (`stage14_critic`)
- [x] Stage 15 (`stage15_analysis_agent`)
- [x] Stage 16 (`stage16_three_specialist_supervisor`)
- [x] Final combined multi-agent system (`stage17_final_multi_agent_system`)
- [x] Stage 18 — durable Postgres checkpointing (`stage18_postgres_persistence`)
- [x] Stage 19 — FastAPI HTTP backend (`stage19_fastapi_backend`)
- [x] Stage 20 — document upload & ingestion (`stage20_document_upload`)
- [x] Stage 21 — embeddings & semantic vector search (`stage21_semantic_search`)
- [x] Stage 22 — Knowledge Agent RAG over uploaded documents (`stage22_knowledge_agent_rag`)
