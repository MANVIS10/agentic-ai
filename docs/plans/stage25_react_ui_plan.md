# Stage 25 Implementation Plan: React Frontend

## Context

`docs/specs/stage25_react_ui_spec.md` (approved, including its two
confirmed backend additions in §3.1/§3.2) defines Stage 25: a React +
TypeScript single-page app that gives Stage 24's FastAPI backend a real
UI — document upload/list, chat, human approval, and an agent execution
trace — without changing the LangGraph architecture, adding agents, or
adding authentication. This plan turns that spec into concrete files and
an implementation order.

Two backend additions are required and already confirmed by the spec:
`GET /documents` (§3.1) and a `trace` field on `ThreadStatusResponse`
(§3.2), plus CORS middleware (§3.3, needed for any browser client to
reach the API). All three live in a **new** `stages/stage25_react_ui/backend/main.py`
— a duplicate of `stages/stage24_security_guardrails/main.py` plus exactly these
changes — per this project's "duplicate, don't edit" convention.
`stages/stage24_security_guardrails/` is not touched.

## Files

**Created:**
- `stages/stage25_react_ui/backend/main.py` — Stage 24's file + §1 below
- `stages/stage25_react_ui/backend/test_react_ui_backend.py` — standalone script
  (asserts + prints, `TestClient`, real Postgres/OpenAI), covering the
  backend delta only
- `stages/stage25_react_ui/` — Vite React+TS app: `package.json`, `tsconfig.json`,
  `vite.config.ts`, `.env.example`, `src/**` (laid out in spec §12)
- `stages/stage25_react_ui/README.md` — per `CLAUDE.md`'s convention

**Not touched:** every `stage1-24_*/` folder, `requirements.txt`,
`docker-compose.yml`. Top-level `README.md`/`PROGRESS.md` updates are
deferred to a separate step after implementation + testing are verified
(matching how Stage 24 handled it) — not part of this plan.

---

## 1. Minimal FastAPI backend changes

All in `stages/stage25_react_ui/backend/main.py`, a copy of Stage 24's file.

**CORS (§9 of this plan / spec §3.3):**
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite's default dev origin
    allow_credentials=False,       # no cookies/auth headers used anywhere
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
```
`CORSMiddleware` answers the browser's `OPTIONS` preflight automatically,
before it reaches any route, rate limiter, or thread lock — no
interaction with existing guards to account for.

**`GET /documents` (spec §3.1, §4.6):**
```python
class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    created_at: str

class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]

LIST_USER_RATE_LIMIT = (30, 60)
LIST_IP_RATE_LIMIT = (90, 60)

@app.get("/documents", response_model=DocumentListResponse)
def list_documents(user_id: str, http_request: Request):
    user_id = _validate_text_field(user_id, "user_id")
    client_ip = http_request.client.host if http_request.client else "unknown"
    _enforce_rate_limits("list", user_id, client_ip, LIST_USER_RATE_LIMIT, LIST_IP_RATE_LIMIT)
    rows = pg_conn.execute(
        "SELECT id, filename, file_type, chunk_count, uploaded_at AS created_at "
        "FROM documents WHERE user_id = %s ORDER BY uploaded_at DESC",
        (user_id,),
    ).fetchall()
    return DocumentListResponse(documents=[
        DocumentSummary(document_id=str(r[0]), filename=r[1], file_type=r[2],
                         chunk_count=r[3], created_at=r[4].isoformat())
        for r in rows
    ])
```
Reuses `_validate_text_field` and `_enforce_rate_limits` (Stage 24,
unchanged) with the existing `"list"`-scope pattern established for
`"chat"`/`"upload"`/`"search"`.

**Execution trace (spec §3.2, §4.3) — the one genuinely non-trivial
change.** The spec's own reasoning ("research_subtask() already has
`result["next"]`/`result["verdict"]`/`result["retry_count"]` sitting
unused") is correct for the supervisor/critic decisions, but **tool
names are not actually visible at that point today**: `research_node`/
`knowledge_node`/`analysis_node` each call their specialist subgraph and
already discard everything except the final message —
`result = research_graph.invoke(...); return {"messages": [result["messages"][-1]]}`
— so the `ToolMessage` entries proving a tool ran never leave that
function. Capturing tool names requires touching those three node
functions, not just `research_subtask()`. Concretely:

1. Add `tools_used: list[str]` to `CriticState`.
2. In each of `research_node`/`knowledge_node`/`analysis_node`, after
   `result = <subgraph>.invoke(...)`, extract tool names from
   `result["messages"]` before truncating it — same
   `isinstance(m, ToolMessage)` check Stage 16/22/23's own test files
   already use (`used_tool()`), just generalized to collect every name
   instead of checking for one:
   ```python
   tool_names = [m.name for m in result["messages"] if isinstance(m, ToolMessage)]
   return {"messages": [result["messages"][-1]], "tools_used": tool_names}
   ```
   `knowledge_node` keeps its existing leak-guard check unchanged, only
   gaining this one extra returned field.
3. Add `trace: list[dict]` to `PlannerState` (plain dicts, not a Pydantic
   model — matching how graph state elsewhere in this file is always a
   `TypedDict`/plain dict, never a `BaseModel`; `SubtaskTrace` stays a
   pure HTTP-layer response type). Reset it in `plan()` alongside
   `results`/`current_index`/etc.
4. In `research_subtask()`, after `result = supervisor_critic_graph.invoke(...)`,
   append one entry:
   ```python
   trace_entry = {
       "subtask": subtask,
       "specialist": result["next"],
       "tools_used": result.get("tools_used", []),
       "status": "completed",
       "verdict": result["verdict"],
       "retry_count": result["retry_count"],
   }
   return {
       "results": state["results"] + [answer],
       "trace": state["trace"] + [trace_entry],
       "current_index": state["current_index"] + 1,
   }
   ```
   On a retry, the specialist node runs again and its return value
   (including `tools_used`) simply overwrites the prior attempt's in
   `CriticState` before `critic_node` re-runs — by construction, the
   trace entry always reflects the attempt that ultimately passed, the
   same way `verdict`/`next` already do.
5. Add `SubtaskTrace` (Pydantic) and `trace: list[SubtaskTrace] | None`
   on `ThreadStatusResponse` (spec §3.2's exact shape, including
   `status: Literal["completed"]`).
6. In `chat()`'s response construction, no change (a `/chat` response is
   always `"awaiting_approval"`, never carries a trace). In `approve()`,
   build `trace=[SubtaskTrace(**entry) for entry in result.get("trace", [])]`
   and pass it into the returned `ThreadStatusResponse`. `reject()`
   passes `trace=[]` (or omit — no research ran).

**Nothing else changes.** No node added/removed, no edge/conditional-
routing function touched, no prompt changed, no new tool. `critic_node`,
`route_from_critic`, `route_from_supervisor`, the graph builders, and
every constant/model not listed above are copied verbatim from Stage 24.

---

## 2. React project setup

`stages/stage25_react_ui/` (project root for this stage, `backend/` as a
subfolder per §1):

- Scaffold with Vite's `react-ts` template (`npm create vite@latest . --
  --template react-ts`), the standard, minimal way to get React + TS +
  a dev server with zero hand-written build config.
- `package.json` dependencies kept to exactly what's needed: `react`,
  `react-dom`, `typescript`, `vite`, `@vitejs/plugin-react`. No `axios`
  (native `fetch` instead — spec §12), no state-management library
  (React Context — spec §12), no CSS framework (CSS Modules — spec §12,
  §15's default recommendation).
- `tsconfig.json`: `strict: true`. Catches API-shape mistakes at compile
  time, which matters more here than usual since the whole point of
  `api/types.ts` (§3 below) is that nothing silently drifts from the
  backend contract.
- `.env.example`: `VITE_API_BASE_URL=http://127.0.0.1:8000`.
- `vite.config.ts`: default `@vitejs/plugin-react` setup; dev server on
  its default port `5173` (must match the backend's CORS allow-list, §1).

---

## 3. API client

- `src/api/types.ts` — every request/response shape from spec §4,
  field-for-field: `ChatRequest`, `ApproveRequest`, `RejectRequest`,
  `ThreadStatusResponse`, `SubtaskTrace`, `UploadResponse`,
  `DocumentSummary`, `DocumentListResponse`, plus a small `ApiError` type
  (`{ detail: string; status: number }`). No `any`.
- `src/api/client.ts` — one function, `apiFetch<T>(path, init)`, reading
  `import.meta.env.VITE_API_BASE_URL`, calling `fetch`, and on a
  non-2xx response parsing `{ detail }` from the JSON body and throwing
  an `ApiError` carrying only that string (never the raw body, never
  `response.statusText`) — the single place §4.8/§10's "relay `detail`
  only" rule is implemented. Every other API module goes through this.
- `src/api/chat.ts` — `postChat`, `postApprove`, `postReject`, each a
  thin typed wrapper around `apiFetch`.
- `src/api/documents.ts` — `uploadDocument(file, userId)` (builds
  `FormData`, calls `fetch` directly with no `Content-Type` header set —
  required so the browser sets the multipart boundary itself — parsing
  the response/error the same way `apiFetch` does, duplicated narrowly
  rather than forcing multipart through the JSON-shaped wrapper) and
  `listDocuments(userId)` (a plain `apiFetch` GET).

---

## 4. Document upload/list UI

- `DocumentUploader.tsx`: drop zone + `<input type="file">` fallback,
  `accept=".pdf,.txt,.docx"`. Client-side pre-check against a shared
  `MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024` constant (`src/api/types.ts`
  or a small `constants.ts`) and extension — on failure, shows a
  friendly inline message *without calling the API*; on pass, calls
  `uploadDocument` via `useDocuments`. Indeterminate spinner during the
  call (spec §5, §15 — no `XMLHttpRequest` progress tracking for v1).
- `DocumentSidebar.tsx` / `DocumentListItem.tsx` / `DocumentEmptyState.tsx`:
  render `useDocuments().documents`; empty state when the array is
  empty (a valid `200`, not an error — spec §4.6).
- `useDocuments.ts` hook: `{ documents, loading, error, refresh(), upload(file) }`.
  Fetches once on mount (current identity, §5 below) and after a
  successful upload (`refresh()` — re-fetching `GET /documents` rather
  than optimistic-merging the `UploadResponse` shape, the simpler of
  spec §15's two options and the one this plan picks).

---

## 5. Chat UI

- `state/AppContext.tsx`: holds the identity (`userId`, from
  `useIdentity`), and is the one place `userId` is read from for every
  API call (spec §9's "sourced from exactly one place" rule).
- `useIdentity.ts`: reads/writes a `user_id` string in `localStorage`;
  `IdentityPrompt.tsx` shown on first load if none is stored yet (plain
  text input, explicitly not styled or labeled as a login — spec §9).
- `useChat.ts`: owns `{ threadId, phase, subtasks, approvalPrompt, finalAnswer, trace, error }`
  where `phase` is `"idle" | "planning" | "awaiting_approval" | "researching" | "completed" | "rejected"`.
  `threadId` is generated with `crypto.randomUUID()` on first use or on
  "New chat"; `ask(question)` calls `postChat`, `approve()`/`reject()`
  call the matching endpoint. Each transitions `phase` before/after the
  call so components never infer state from `undefined` fields.
- `ChatArea.tsx` / `MessageList.tsx` / `MessageBubble.tsx` /
  `ChatInput.tsx` / `ChatEmptyState.tsx`: render per `phase` (spec §6,
  §11's layout). `ChatInput` is disabled whenever `phase` is anything
  other than `"idle"`/`"completed"`/`"rejected"` (spec §6's "don't let a
  user race their own in-flight request" rule).

---

## 6. Agent execution trace UI

- `ExecutionTracePanel.tsx`: renders `useChat().trace` once `phase ===
  "completed"`; `TraceEmptyState.tsx` otherwise (spec §7's "populates
  once, after the fact" behavior — matches the backend genuinely having
  no streaming, §1).
- `SubtaskTraceEntry.tsx`: one row per entry — subtask text, a
  specialist badge (`research`/`knowledge`/`analysis` mapped to a
  friendly label, not the raw string), `tools_used.join(", ")` or "no
  tool used" if empty, and a verdict badge ("passed" / "needed one
  retry" when `retry_count > 0` — never phrased as a failure, spec §7).
  Renders only the five `SubtaskTrace` fields — no path exists in this
  component (or anywhere in `api/types.ts`) for a system prompt, a
  credential, or raw tool output to flow through, since the type itself
  doesn't carry them (spec §3.2, §10).

---

## 7. Human approval UI

- `ApprovalPanel.tsx`: shown when `phase === "awaiting_approval"`.
  Renders `subtasks` as a numbered list and `approvalPrompt` verbatim,
  with two buttons, **Approve** and **Reject** (not a toggle, not a
  y/n text field — spec §8). Both disable immediately on click
  (`phase` flips to `"researching"` synchronously before the network
  call even starts, so a second click has nothing to do).
- Approve → `useChat().approve()` → `POST /approve` → on success,
  `phase = "completed"`, `finalAnswer`/`trace` populated. Reject →
  `useChat().reject()` → `POST /reject` → `phase = "rejected"`, shown
  plainly in the chat area as a declined plan, not an error (spec §8).

---

## 8. Error/loading states

- `LoadingIndicator.tsx`: takes a `label` prop; three call sites use
  distinct copy — "Planning…" (`/chat` in flight), "Researching… this
  can take a little while" (`/approve` in flight), "Uploading…"
  (`/documents/upload` in flight) — never one generic spinner reused
  verbatim everywhere (spec §6, §11).
- `ErrorBanner.tsx`: renders `error.detail` only, styled distinctly
  (a consistent inline treatment, not a full-page takeover) so a failed
  upload doesn't block the chat area and a failed chat call doesn't
  block the sidebar (spec §11). Used in `DocumentUploader`, `ChatArea`
  (for `/chat`/`/approve`/`/reject` failures), and `DocumentSidebar`
  (for `/documents` list failures).

---

## 9. CORS

Covered concretely in §1 above — `CORSMiddleware`, allow-listing only
`http://localhost:5173`, `allow_credentials=False`, methods restricted to
`GET`/`POST`, headers restricted to `Content-Type`. No wildcard origin.

---

## 10. Testing

**Backend (`stages/stage25_react_ui/backend/test_react_ui_backend.py`)** —
standalone script, `TestClient`, real Postgres/OpenAI (project
convention, no mocking):
- `GET /documents` returns only the calling user's documents (two-user
  isolation check, same pattern as Stage 23's own test file), with the
  exact field shape (`document_id`, `filename`, `file_type`,
  `chunk_count`, `created_at`) and correct ordering (most recent first).
- `GET /documents` for a brand-new `user_id` returns `200`, `{"documents": []}`,
  not an error.
- `POST /approve` for a research-type question returns a `trace` whose
  entries have `specialist`/`tools_used`/`status`/`verdict`/`retry_count`
  all populated and internally consistent (e.g. a knowledge question's
  entry has `specialist: "knowledge"` and `"search_uploaded_documents"`
  in `tools_used`) — reusing this project's existing pattern of
  asserting against real LLM behavior rather than mocking it.
- `trace` never contains any of §3.2's excluded strings (system prompt
  text, `OPENAI_API_KEY`, `DATABASE_URL`) — a direct string-search
  assertion against the serialized response, mirroring Stage 24's own
  error-hygiene test style.
- A CORS preflight `OPTIONS` request from the allowed origin gets
  `Access-Control-Allow-Origin: http://localhost:5173`; a disallowed
  origin does not.
- **Regression**: re-run Stage 24's own `test_security_guardrails.py`
  unmodified against this stage's `app` (same "import the new module's
  `app`/`pg_conn`, run the old test file's `run()`" pattern Stage
  22/23 already used) — confirms none of §1's additions weakened any
  existing guardrail.

**Frontend** — no new JS test framework introduced (this project has
never used one; adding Jest/Vitest/Playwright here would be a real new
dependency for a UI this small). Instead, a manual verification
checklist in `stages/stage25_react_ui/README.md`, walked through against the
real running backend + real OpenAI in an actual browser: upload a
document → see it in the sidebar → ask a question about it → see the
plan → Approve → see the final answer *and* a trace entry showing
`knowledge` + `search_uploaded_documents` → start a new chat, ask a
question, Reject the plan → declined state shown, not an error → trigger
each error case (unsupported file type, empty question, a question with
no uploaded documents) and confirm the exact backend `detail` string
appears → change identity and confirm the document list/chat/trace all
reset. This is the same "confirmed directly against the real backend"
philosophy every prior stage's test file already follows, applied
through a browser instead of `TestClient` since that's the surface being
verified.

---

## 11. Running React + FastAPI together

Documented in `stages/stage25_react_ui/README.md`, three independent processes
(no reverse proxy, no combined server — out of scope per spec §14):

```
docker compose up -d                              # Postgres (unchanged)
python stages/stage25_react_ui/backend/main.py            # FastAPI on :8000
cd stage25_react_ui && npm install && npm run dev  # Vite on :5173
```

`.env.example` documents `VITE_API_BASE_URL`; `stages/stage25_react_ui/backend/`
reads the same root `.env` (`OPENAI_API_KEY`, `DATABASE_URL`) every
other stage's `main.py` already does.

---

## Implementation order

1. Backend delta (`stages/stage25_react_ui/backend/main.py`): CORS → `GET
   /documents` → `CriticState.tools_used` threading through the three
   specialist nodes → `PlannerState.trace` → `SubtaskTrace`/
   `ThreadStatusResponse.trace` → `approve()`'s response construction.
2. Backend test file; run against the real Postgres/OpenAI backend; fix
   any failures; regression-run Stage 24's test file against this app.
3. Scaffold the Vite React+TS project (§2).
4. `api/types.ts`, `api/client.ts`, `api/chat.ts`, `api/documents.ts` (§3).
5. `state/AppContext.tsx` + `useIdentity`/`useDocuments`/`useChat` hooks.
6. Layout shell (`Header`, `AppLayout`) + common components
   (`ErrorBanner`, `LoadingIndicator`, `IdentityPrompt`).
7. Document components (§4).
8. Chat components (§5).
9. Approval panel (§7).
10. Trace panel components (§6).
11. CSS Modules styling pass, applied consistently across the above.
12. Manual end-to-end verification against the real running backend +
    real OpenAI, walking the full checklist in §10.
13. Write `stages/stage25_react_ui/README.md` (what was added, architecture,
    how to run §11, the manual verification checklist, diff vs. Stage 24).
14. *Deferred, separate step, not part of this plan*: top-level
    `README.md`/`PROGRESS.md` updates, only after 1-13 are verified
    passing — matching how Stage 24 handled its own doc updates as a
    distinct final step gated on a green test run.

## Verification

- `python stages/stage25_react_ui/backend/test_react_ui_backend.py` passes
  against the real Postgres/OpenAI backend.
- Stage 24's own `test_security_guardrails.py` still passes, unmodified,
  against `stages/stage25_react_ui/backend/main.py`'s `app` — confirms §1 is
  additive-only.
- The manual browser checklist in §10 is walked through and confirmed by
  whoever reviews the implementation — this can't be fully automated
  without a headless-browser dependency this project hasn't taken on
  anywhere else (out of scope per the spec's minimal-dependency stance,
  same reasoning as skipping a JS test framework above).
