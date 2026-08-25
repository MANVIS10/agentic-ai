# Current Flow (Stage 25)

This describes the end-to-end flow of the most complete version of the app —
`stages/stage25_react_ui/` — which wraps every prior stage's concepts into one
running system. Earlier stages (1-24) still exist as standalone, untouched
snapshots of one concept each; this file only documents the current
"full system" state.

## High level

```
React + TypeScript UI  --HTTP-->  FastAPI backend  --.invoke()/Command(resume)-->  LangGraph app
      (frontend/)                    (backend/main.py)                  (planner -> supervisor+critic -> specialists)
                                            |
                                            v
                                    Postgres (pgvector)
                              checkpoints + documents/document_chunks
```

## Request flow: `POST /chat` -> `POST /approve` / `POST /reject`

1. **Planner** (`stage6_planner` origin) — an LLM breaks the user's question
   into 2-3 subtasks.
2. **Human approval gate** (`stage7_human_in_loop` origin) — the graph calls
   `interrupt()` to show the plan and pause execution. `/chat` returns the
   pending plan to the UI; the approve/reject buttons resume the paused graph
   via `Command(resume=...)` against `/approve` or `/reject`. Rejection
   routes straight to `END` with no research done.
3. **Per-subtask research loop** — once approved, each subtask is handed to
   an inner **supervisor + critic** graph (`stage13`/`stage14` origin):
   - **Supervisor** — structured LLM output routes the subtask to one of
     three specialist subgraphs.
   - **Specialists**:
     - Research Agent -> `search_web` (DuckDuckGo)
     - Knowledge Agent -> `search_uploaded_documents` (RAG over the
       *calling user's own* uploaded docs, via pgvector; bundled
       `knowledge_base/*.md` is not used here)
     - Analysis Agent -> `calculate` (safe AST-based arithmetic evaluator)
   - **Critic** — judges the specialist's answer (pass/retry) and can send
     one bounded retry back to the *same* specialist with feedback attached.
4. **Synthesis** — the planner combines all subtask answers into one final
   response.
5. **Persistence** — every step's state is checkpointed to **Postgres** via
   `PostgresSaver` (`stage18` origin), so a paused-for-approval or completed
   conversation survives a backend process restart.
6. **Trace** — `research_node`/`knowledge_node`/`analysis_node` each capture
   which tool actually ran (via `ToolMessage` inspection) and surface it as
   `tools_used` on `CriticState`; the API exposes this as a `trace` field on
   `ThreadStatusResponse` for the UI's trace panel. The panel populates once,
   after `/approve` returns — there's no streaming transport, so it's not a
   live feed.

## Document / RAG side

- `POST /documents/upload` — validates the file, extracts text
  (PDF/TXT/DOCX), chunks it (`RecursiveCharacterTextSplitter`), embeds each
  chunk (OpenAI embeddings), and stores it in Postgres (`documents`,
  `document_chunks` with a `pgvector` `embedding` column) scoped to a
  `user_id`.
- `POST /documents/search` — cosine-similarity search (`<=>`) over a user's
  own chunks, with `top_k`/threshold/document scoping.
- `GET /documents` — lists a user's uploaded documents for the UI.
- Isolation: every retrieval path (`search_uploaded_documents` tool included,
  via `InjectedState` so the LLM can never supply someone else's `user_id`)
  filters by `user_id`, so one user's uploads are never returned to another
  user.

## Hardening (Stage 24, carried into 25)

No new capability — narrows what malformed/malicious input can do:

- File/type/size validation; dangerous files (corrupt, PDF page-count bomb,
  DOCX zip bomb, extraction timeout) all collapse into one identical generic
  `422` (no tuning oracle for attackers).
- Retrieved document content is wrapped in an explicit untrusted-data
  envelope for the Knowledge Agent's system prompt (prompt-injection
  framing, not content filtering). Scoped to the Knowledge Agent only —
  `search_web` carries the same class of risk but is a known, unaddressed
  gap.
- Output leak guard on responses.
- Per-route, per-`user_id`+IP in-process rate limiting (`/chat`,
  `/documents/upload`, `/documents/search` each have independent budgets).
- No authentication — `user_id` is still self-asserted by the caller.

## What's explicitly out of scope / not built

- No streaming transport (trace and chat responses are one blocking call).
- No authentication/sessions/API keys.
- No external rate-limiting infra (Redis, etc.) — in-process dict only, no
  TTL eviction.
- No content-based filtering of "suspicious" document text.

## Stage map (for reference)

| Stage | Concept added |
|---|---|
| 1 | `StateGraph` + `MemorySaver`, per-thread chat memory |
| 2 | Tool calling / ReAct loop (`bind_tools`, `ToolNode`, `tools_condition`) |
| 3 | RAG over local markdown (`search_knowledge_base`) |
| 4 | HTTP fetch + HTML parsing (`fetch_webpage`) |
| 5 | PDF download + text extraction (`fetch_pdf`) |
| 6 | Planner: question -> subtasks -> synthesis |
| 7 | Human-in-the-loop approval (`interrupt()`/`Command(resume=...)`) |
| 8 | Planner subtasks researched by a 4-tool agent |
| 9 | Long-term memory (JSON file, outside graph state) |
| 10 | Flat multi-tool agent (tool selection, no planner) |
| 11-12 | Specialist agents (narrow toolset + identity) |
| 13-14 | Supervisor routing + critic retry loop |
| 15-16 | Third specialist (Analysis) + three-way supervisor/critic |
| 17 | Final multi-agent system: planner + approval wraps supervisor+critic |
| 18 | Postgres-backed checkpointing (`PostgresSaver`) |
| 19 | FastAPI HTTP API around the graph |
| 20 | Document upload + storage (no embeddings yet) |
| 21 | Embeddings + semantic search (`pgvector`) |
| 22 | Knowledge Agent rewired to RAG over uploaded docs |
| 23 | Per-user document isolation |
| 24 | Security guardrails (hardening only) |
| 25 | React frontend on top of Stage 24's API |
