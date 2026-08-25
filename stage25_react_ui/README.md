# Stage 25: React Frontend

## What was added

A React + TypeScript single-page app that gives Stage 19-24's FastAPI
backend a real user interface — document upload/listing, chat, human
approval, and an agent execution trace — reachable through a browser
instead of `curl`, `TestClient`, or a terminal REPL. This is a **client**,
not a redesign: nothing about the LangGraph graph (planner, supervisor,
critic, three specialists) or the RAG pipeline changes. React talks to
the same JSON/multipart HTTP contract `curl`/`TestClient` already used,
and trusts the backend for every correctness and security guarantee it
already provides. See
[`.claude/spec/stage25_react_ui_spec.md`](../.claude/spec/stage25_react_ui_spec.md)
for the full spec and
[`.claude/plans/stage25_react_ui_plan.md`](../.claude/plans/stage25_react_ui_plan.md)
for the implementation plan.

**Explicitly not added, per the spec's scope**: authentication of any
kind, new agents/tools, any change to LangGraph routing/composition,
streaming/WebSocket transport, or a JS test framework/headless-browser
dependency.

## New concept

**A frontend only needs two additive backend changes to become
possible**, even when eight prior stages built the whole HTTP contract:
`GET /documents` (the `documents` table already had every column a
listing needed — `filename`, `file_type`, `chunk_count`, `uploaded_at` —
it was just never `SELECT`ed by any route) and a `trace` field on
`ThreadStatusResponse` (the supervisor's routing decision, the critic's
verdict, and which tool a specialist called all already existed in memory
inside `research_subtask()`/`research_node`/`knowledge_node`/
`analysis_node` — only ever handed to `print()`, never returned over
HTTP). Both are read-only/data-plumbing changes against data the graph
already computes; neither adds a capability, an agent, or changes how the
graph routes or executes.

**The backend is fully synchronous** (`POST /approve` is one blocking
call that runs the *entire* subtask loop before returning) — so the
execution trace panel is designed as a panel that populates once, after
the fact, never a live step-by-step feed. Building a live feed would mean
adding streaming infrastructure this stage was explicitly asked not to
add.

## Architecture

```
Browser (React SPA, Vite dev server :5173)
      |
      | fetch (JSON, or multipart for uploads)
      v
CORSMiddleware (new - allow-lists http://localhost:5173 only)
      |
      v
FastAPI app (stage25_react_ui/backend/main.py, uvicorn :8000)
      |
      +-- GET /documents?user_id=...            <- new (spec §3.1)
      |         -> _validate_text_field, _enforce_rate_limits("list", ...)
      |         -> SELECT ... FROM documents WHERE user_id = %s
      |
      +-- POST /chat, /approve, /reject          <- unchanged routes,
      |         (Stage 19-24, byte-identical         /approve's response
      |          logic)                              gains `trace` (new)
      |
      +-- POST /documents/upload, /documents/search, /documents/backfill-embeddings
                (unchanged, Stage 20-24)

research_subtask() -> supervisor_critic_graph.invoke(...)
      |
      +-- research_node / knowledge_node / analysis_node
      |         -> <specialist>_graph.invoke(...)
      |         -> NEW: tool_names = [m.name for m in result["messages"]
      |                               if isinstance(m, ToolMessage)]
      |         -> returns {"messages": [...], "tools_used": tool_names}
      |
      +-- NEW: trace_entry = {subtask, specialist: result["next"],
      |         tools_used, status: "completed",
      |         verdict: result["verdict"], retry_count}
      |   appended to PlannerState["trace"]
      v
approve() builds trace=[SubtaskTrace(**e) for e in result["trace"]]
```

```
src/
├── api/          client.ts (one fetch wrapper, parses `detail` only),
│                 chat.ts, documents.ts, types.ts (mirrors backend models)
├── state/        AppContext.tsx - identity/documents/chat, one shared place
├── hooks/        useIdentity, useDocuments, useChat
└── components/
    ├── layout/   Header, AppLayout (3-panel shell, responsive drawers)
    ├── documents/ DocumentSidebar, DocumentUploader, DocumentListItem, ...
    ├── chat/     ChatArea, MessageList, ApprovalPanel, ChatInput, ...
    ├── trace/    ExecutionTracePanel, SubtaskTraceEntry, TraceEmptyState
    └── common/   ErrorBanner, LoadingIndicator, IdentityPrompt
```

## The backend delta, area by area

### 1. CORS (spec §3.3)

`CORSMiddleware`, allow-listing only `http://localhost:5173` (Vite's
default dev origin) — never `"*"`. `allow_credentials=False` (no
cookies/auth headers anywhere in this project), methods restricted to
`GET`/`POST`, headers restricted to `Content-Type`. Answers the browser's
`OPTIONS` preflight automatically, before any route/rate-limiter/thread
lock runs.

### 2. `GET /documents` (spec §3.1)

Returns only the calling `user_id`'s documents, most recently uploaded
first — same `WHERE user_id = %s` isolation filter Stage 23 already
applies on every other retrieval path, same `_validate_text_field`/
`_enforce_rate_limits` pattern every other `user_id`-scoped route already
uses (`LIST_USER_RATE_LIMIT = (30, 60)`, `LIST_IP_RATE_LIMIT = (90, 60)`).
An empty list is a valid `200`, not an error.

### 3. Execution trace (spec §3.2)

The one genuinely non-trivial change. `research_node`/`knowledge_node`/
`analysis_node` each ran their specialist subgraph and discarded
everything except the final message — the `ToolMessage` entries proving a
tool ran never left those functions. Now each one also extracts tool
names (the same `isinstance(m, ToolMessage)` check Stage 16/22/23's own
test files already use) and returns them as `tools_used` on `CriticState`.
`research_subtask()` combines that with `result["next"]` (the
supervisor's routing decision) and `result["verdict"]`/`result["retry_count"]`
(the critic's judgment) — already computed, previously only `print()`ed —
into one `trace_entry` per subtask, appended to `PlannerState["trace"]`.
`approve()` returns it as `trace: list[SubtaskTrace]`; `reject()` returns
`trace: []` since no research ran.

Deliberately excluded from `SubtaskTrace` (five fields only: `subtask`,
`specialist`, `tools_used`, `status`, `verdict`, `retry_count`): no system
prompt text, no API keys/connection strings, no raw tool arguments or
output, no critic `feedback` text.

## How to run

Three independent processes, no reverse proxy or combined server:

```
docker compose up -d
.venv\Scripts\activate
pip install -r requirements.txt
python stage25_react_ui/backend/main.py
```

```
cd stage25_react_ui
npm install
cp .env.example .env.local      # VITE_API_BASE_URL=http://127.0.0.1:8000
npm run dev                      # Vite on http://localhost:5173
```

Open `http://localhost:5173`. First load asks for a display name (not a
login — see "Identity" below), then shows the three-panel app.

**Use `.env.local`, not `.env`, for this file.** Vite loads either name,
but the backend's own `load_dotenv()` (`stage25_react_ui/backend/main.py`)
walks up from its own directory looking for a file literally named
`.env` and stops at the first one it finds — a `stage25_react_ui/.env`
would shadow the real repo-root `.env` (`OPENAI_API_KEY`/`DATABASE_URL`)
before the backend ever saw it, and the backend would silently start with
those missing whenever it's launched with `stage25_react_ui/` as part of
the lookup path. `.env.local` sidesteps that entirely - Vite still reads
it, `find_dotenv()` skips past it - and it's already covered by this
folder's own `.gitignore` (`*.local`).

## Identity

There's no authentication in this project (Stage 23/24's trust model,
unchanged). The first-load prompt asks for a plain display name, stored
in `localStorage`, and shown in the header — exactly as much identity as
`thread_id` already provided, just made visible. Switching identity
("change" in the header) clears the document list, chat thread, and trace
panel and re-fetches fresh for the new name; it never mixes two
identities' data.

## Testing

**Backend** (`stage25_react_ui/backend/test_react_ui_backend.py` —
standalone script, `TestClient`, real Postgres/OpenAI, no mocking):

```
python stage25_react_ui/backend/test_react_ui_backend.py
```

Covers: `GET /documents` two-user isolation and exact field shape; a
brand-new `user_id` gets `200 {"documents": []}`, not an error; a full
`/chat` -> `/approve` round trip for a knowledge question returns a
`trace` whose `knowledge` entry includes `search_uploaded_documents` in
`tools_used`; `/reject` returns `trace: []`; the trace response never
contains `KNOWLEDGE_SYSTEM_PROMPT` text or `OPENAI_API_KEY`/
`DATABASE_URL` fragments; CORS allows `http://localhost:5173` and rejects
an unlisted origin; and, as a regression check, Stage 24's own
`test_security_guardrails.py` is imported and run **unmodified** against
this module's `app`, confirming the additions above didn't weaken any
existing guardrail.

All checks pass — full output confirms two-user document isolation, the
knowledge-question trace round trip, CORS allow/deny, and a clean
Stage 24 regression run.

**Frontend**: no new JS test framework (this project has never used one).
Verified instead by an actual manual walkthrough against the real running
backend + real OpenAI, in a real browser (Chrome, via the `claude-in-chrome`
extension):

- ✅ Uploaded a `.txt` document — appeared in the sidebar with filename,
  type, and chunk count, no page reload.
- ✅ Asked a question about the uploaded document's content — "Planning…"
  loading state showed, then the approval panel rendered the plan
  (numbered subtasks) and the backend's exact `approval_prompt` text.
- ✅ Clicked Approve — "Researching… this can take a little while" showed
  for the duration of the blocking `/approve` call, then the synthesized
  final answer appeared in the chat (correctly citing the uploaded
  document's content) **and** the execution trace panel populated with
  three entries, each showing `Knowledge Agent` +
  `Tool: search_uploaded_documents` + `passed` — directly confirming
  acceptance criterion 4 (the Knowledge Agent searches only uploaded
  documents, made observable via the trace).
- ✅ Started a new chat, asked an arithmetic question, clicked Reject —
  shown plainly as "Plan declined — no research was run" (not styled as
  an error), trace panel correctly stayed in its empty state.
- ✅ Triggered the unsupported-file-type case client-side (an `.exe`) —
  friendly inline message ("This file type looks unsupported..."), no
  network call fired, rest of the UI stayed fully usable.
- ✅ Switched identity via "change" — document list, chat, and trace all
  reset to empty for the new name; confirmed the new identity's document
  list never showed the previous identity's uploaded file.
- ✅ Checked the browser console throughout — no errors.
- ✅ Dark mode rendered correctly automatically (`prefers-color-scheme`),
  no explicit toggle needed for this pass.

**Not verified in this pass**: the sub-860px responsive drawer collapse
(`AppLayout.module.css`'s media query) — the automated browser session's
window couldn't be narrowed below its default width. The CSS is
standard (`@media (max-width: 860px)`, the same mechanism used
throughout `src/styles`) and was reviewed, not executed narrow. Worth a
quick manual check in a real narrow window or devtools device toolbar
before considering this fully closed.

## What changed compared with Stage 24

| | Stage 24 | Stage 25 |
|---|---|---|
| Frontend | None — `curl`/`TestClient`/REPL only | React + TypeScript SPA (`stage25_react_ui/src`) |
| CORS | None (no browser caller) | `CORSMiddleware`, allow-listing `http://localhost:5173` only |
| `GET /documents` | Did not exist | New route, user-scoped, rate-limited |
| `ThreadStatusResponse` | `thread_id`, `status`, `subtasks`, `approval_prompt`, `results`, `final_answer` | + `trace: list[SubtaskTrace] \| None` |
| `CriticState` | No `tools_used` field | + `tools_used: list[str]`, populated by each specialist node |
| `PlannerState` | No `trace` field | + `trace: list[dict]`, appended to by `research_subtask()` |
| Research Agent, Analysis Agent, supervisor, critic routing, planner, `/chat`, `/reject`, `/documents/upload`, `/documents/search`, `/documents/backfill-embeddings`, `/health`, Stage 23 isolation, Stage 24 guardrails | — | Byte-identical to Stage 24 |
| New dependencies (backend) | — | None (`CORSMiddleware` ships with `fastapi`) |
| New dependencies (frontend) | — | `react`, `react-dom`, `typescript`, `vite`, `@vitejs/plugin-react` only — no `axios`, no state-management library, no CSS framework |

Stage 24 proved the backend could be hardened at its edges without
restructuring any routing, review, or planning layer above it. Stage 25
proves the same app can grow a full browser-based UI on top of that
hardened backend by touching only two response shapes and adding one
middleware — no LangGraph node, edge, prompt, or tool changed to make a
real user interface possible.
