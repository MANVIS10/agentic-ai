# Stage 25 Specification — React Frontend for the Multi-Agent Research Assistant

## 0. Status

```text
Stage 21  Embeddings + vector search     ✅ (deliberate extension, post-roadmap)
Stage 22  Knowledge Agent RAG            ✅ (deliberate extension, post-roadmap)
Stage 23  Per-user document isolation    ✅ (deliberate extension, post-roadmap)
Stage 24  Security & guardrails          ✅ (deliberate extension, post-roadmap)
Stage 25  React frontend                 → Next (this spec)
```

Like Stage 18-24, Stage 25 is a deliberate extension past the original
roadmap in `spec_document.md`, not a numbered item from it. It is the
**first frontend** in this project — every prior stage has been exercised
through a terminal REPL, `curl`, or `TestClient`. This document is a
**specification only**. No implementation code is written against it yet.

---

## 1. Purpose

Stage 19-24 built a complete HTTP API — planning, human-in-the-loop
approval, document upload, semantic search, per-user isolation, and
security guardrails — but the only way to use any of it today is `curl`,
a Python `TestClient`, or reading server-side `print()` output. Stage 25
gives that API a real user interface: a React single-page app that lets a
person upload documents, ask questions, watch the plan get approved, and
see an answer, without needing to know the API shapes underneath.

**This is a client, not a redesign.** Nothing about the LangGraph graph
(planner, supervisor, critic, three specialists), the multi-agent
architecture, or the RAG pipeline changes. React talks to Stage 24's
FastAPI app exactly as `curl`/`TestClient` already do — over the same
JSON/multipart HTTP contract — and trusts the backend for every
correctness and security guarantee it already provides (input validation,
prompt-injection defense, per-user isolation, rate limiting).

**One honest exception, flagged up front (§3):** two of the ten UI
requirements — listing a user's uploaded documents, and showing which
agent/tool ran — could not be built against Stage 24's API **as it
existed before this stage**, because the data they need was either never
returned over HTTP at all, or only ever `print()`ed to the server's own
stdout. Rather than silently inventing a fake "trace" from data that
isn't there, or quietly dropping those two requirements, §3 defines the
smallest possible, purely additive backend surface that makes them real —
no new agents, no new routing, no graph changes, just returning data the
graph already computes instead of discarding it. **Both additions in §3
are confirmed and in scope for this stage** (approved in review); §3.3
(CORS) remains a separate, still-open implementation detail (§15) — a
browser-hosting prerequisite, not one of the two additions being
confirmed here.

---

## 2. Scope of Stage 25

In scope:

- A React + TypeScript single-page application (§9-§12) that provides all
  ten UI capabilities in the request (§4-§13).
- The exact backend contract it depends on, documented endpoint by
  endpoint (§4), including the two confirmed additive backend changes in
  §3.1/§3.2.
- The trust/identity model the UI uses given Stage 23's `user_id` is
  self-asserted, not authenticated (§8).
- UX requirements: layout, states, responsiveness (§11).
- Acceptance criteria tying every requirement to an observable outcome
  (§13).

Explicitly not in scope — see §14. In particular: **no authentication**,
**no new agents or tools**, **no change to graph routing/composition**,
**no streaming/WebSocket transport** (the backend is fully synchronous
today — see §3.2's consequence for the trace panel, and §14).

---

## 3. Backend Additions Required for the React UI

This section exists because two requirements in the request cannot be
satisfied by Stage 24's API as it stands. **§3.1 (`GET /documents`) and
§3.2 (execution trace) are confirmed, approved, and in scope for Stage
25** — not open proposals. §3.3 (CORS) is a separate, still-open
implementation detail (§15), needed for any browser client to reach the
API at all, but not one of the two additions being locked in here. Each
subsection below states exactly what's needed, why, and the exact
minimal shape it takes.

**Where this code lives, consistent with this project's own convention:**
every stage duplicates the previous stage's `main.py` rather than editing
it in place (`CLAUDE.md`). Stage 25 continues that pattern — §3.1 and
§3.2 live in a **new** `stage25_react_ui/backend/main.py` that is Stage
24's file plus exactly these additions, not an edit to
`stage24_security_guardrails/main.py`. Stage 24 is left byte-for-byte
untouched, same as every earlier stage is untouched by every later one.
Both additions are read-only/data-plumbing changes against tables and
values that already exist — neither adds a capability, an agent, or a
change to how the graph routes or executes (§14 restates this boundary
explicitly).

### 3.1 `GET /documents` — confirmed

Requirement 1 asks for "list user's uploaded documents." Today's API has
no such route. `POST /documents/search` is the closest thing, but it
requires a non-empty `query` and returns semantically *relevant chunks*,
not *the user's documents* — there is no query that reliably means "list
everything," and its response (`SearchResult`) is chunk-shaped
(`chunk_id`, `chunk_index`, `content`, `similarity`), not document-shaped.
The `documents` table already has every column a listing needs
(`filename`, `file_type`, `chunk_count`, `uploaded_at`, `user_id`) — it's
just never selected by any route.

**Must return only documents belonging to the current user** — the exact
same `WHERE user_id = %s` filter Stage 23 already applies on every other
retrieval path (`POST /documents/search`, `search_uploaded_documents`),
applied here too. One terminology note carried consistently from §9:
"current user" means the `user_id` value the request supplies, matching
Stage 23's existing self-asserted trust model — there is no
authentication in this project (§14), so "belonging to the
authenticated... user" in the request is read as "belonging to the
`user_id` the caller is currently using," not as implying a login exists.

**Confirmed shape — `GET /documents`:**

```
GET /documents?user_id={user_id}
```

```python
class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    created_at: str  # ISO 8601 - sourced from documents.uploaded_at (see below)

class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]

@app.get("/documents", response_model=DocumentListResponse)
def list_documents(user_id: str):
    user_id = _validate_text_field(user_id, "user_id")
    rows = pg_conn.execute(
        "SELECT id, filename, file_type, chunk_count, uploaded_at AS created_at "
        "FROM documents WHERE user_id = %s ORDER BY uploaded_at DESC",
        (user_id,),
    ).fetchall()
    return DocumentListResponse(documents=[...])
```

Every field the request asks for is present: `document_id`, `filename`,
`file_type`, `chunk_count`, and `created_at`. One naming reconciliation,
stated plainly rather than left implicit: the `documents` table's actual
column (Stage 20's schema, unchanged since) is `uploaded_at`, not
`created_at` — the SQL above aliases it (`AS created_at`) so the field
name in the HTTP response matches the request exactly, without renaming
the underlying column or touching Stage 20-24's schema at all.

Same isolation filter Stage 23 already applies everywhere else, same
validation helper Stage 24 already has (`_validate_text_field`), same
rate-limiting pattern as the other `user_id`-scoped routes (a new
`LIST_USER_RATE_LIMIT`/`LIST_IP_RATE_LIMIT` pair, reusing
`_enforce_rate_limits` with `scope="list"`). This is a read-only query
against an existing table through the existing permission filter — not a
new capability, not a new agent, not a change to the graph.

### 3.2 Execution trace, via the existing chat/thread response — confirmed

Requirement 3 asks the UI to show which agents/tools ran and their
status. Inside `research_subtask()` and `knowledge_node()`
(`stage24_security_guardrails/main.py`), this data already exists in
memory on every call — `result["next"]` (which specialist the supervisor
picked), `result["verdict"]`/`result["retry_count"]` (the critic's
judgment), and which tool a specialist's subgraph invoked
(`ToolMessage.name`, inspectable exactly the way Stage 16/22/23's own test
files already inspect it) — but every one of those values is only ever
handed to `print()`, never placed in `PlannerState` or returned in
`ThreadStatusResponse`. A React client has no HTTP-visible way to know
that a question was routed to the Knowledge Agent, let alone that it
called `search_uploaded_documents`.

**Exposed through the existing `ThreadStatusResponse`, not a new
endpoint** — per instruction, the trace rides on the same `/chat`
→ `/approve` response the chat flow (§4.2-§4.3) already returns, as one
additional field, rather than a second round trip the UI would have to
correlate back to the same thread/turn itself.

**Confirmed shape — capture and return a trace, deliberately narrow:**

```python
class SubtaskTrace(BaseModel):
    subtask: str
    specialist: Literal["research", "knowledge", "analysis"]  # agent name
    tools_used: list[str]                                     # tool name(s)
    status: Literal["completed"]                               # execution status
    verdict: Literal["pass", "retry"]                          # high-level result
    retry_count: int

class ThreadStatusResponse(BaseModel):
    thread_id: str
    status: Literal["awaiting_approval", "completed", "rejected"]
    subtasks: list[str] | None = None
    approval_prompt: str | None = None
    results: list[str] | None = None
    final_answer: str | None = None
    trace: list[SubtaskTrace] | None = None   # new - only set on "completed"
```

`status: Literal["completed"]` is intentionally a one-value enum today,
not a placeholder oversight: every subtask that reaches `research_subtask`'s
return statement completed by definition (a subtask that raises instead
propagates as a `500` for the *whole* `/approve` call — Stage 24's
existing generic error handling, unchanged — rather than becoming a
per-subtask "failed" entry in a partially-populated trace). Kept as an
explicit field rather than folded into `verdict` so the UI has a plain
"did this run" signal separate from the critic's pass/retry judgment
(§3's two concerns — "ran" vs. "was it good enough" — stay visibly
distinct, matching Stage 14's own critic design where those are already
two different questions).

`PlannerState.results` changes from `list[str]` to carry this alongside
each answer (implementation detail: either a parallel `list[SubtaskTrace]`
field, or promoting `results` to a small structured type — left for the
implementation plan, not this spec). `research_subtask()` already has
every value needed (`result["next"]`, `result["verdict"]`,
`result["retry_count"]`) sitting unused after its `print()` calls; it
additionally captures which tool name(s) appear as `ToolMessage`s in the
specialist subgraph's own `result["messages"]` (the same list Stage
16/22/23's tests already read for exactly this purpose).

**This preserves the existing LangGraph architecture exactly** — no node
is added, removed, or rewired; no edge or conditional-routing function
changes; the supervisor, critic, and three specialists make precisely
the same decisions they already make. The only change is that
`research_subtask()` additionally *records* values it already computes,
instead of only printing them, and `ThreadStatusResponse` returns that
record. Nothing about *how* a question gets answered changes — only
*what the caller is told* about how it was answered.

**Deliberately excluded from the trace, per instruction — this is the
boundary that keeps this a data-plumbing change, not a new capability:**
- **No system prompts, ever, for any specialist** — `RESEARCH_SYSTEM_PROMPT`,
  `KNOWLEDGE_SYSTEM_PROMPT`, `ANALYSIS_SYSTEM_PROMPT`,
  `SUPERVISOR_SYSTEM_PROMPT`, `CRITIC_SYSTEM_PROMPT` are never read by, or
  reachable from, any field on `SubtaskTrace`.
- **No API keys or internal credentials** — `OPENAI_API_KEY`,
  `DATABASE_URL`, and the raw `psycopg` connection are never in scope for
  this response model at all; nothing about the trace's construction
  touches them.
- **No raw tool *arguments* or *outputs*** (a chunk of retrieved document
  text, a web search result body, a calculation result) — only the tool's
  *name* and that it ran. The chat's own `final_answer`/`results` already
  carry the actual content the user is meant to see.
- **No critic `feedback` text** (the one-sentence retry reason) — internal
  review commentary, not something a UI needs to show to satisfy "show
  execution status."
- **No other sensitive/internal data** — no database rows beyond what
  `SubtaskTrace`'s five fields name, no connection strings, no stack
  traces, nothing `unhandled_exception_handler`/existing `HTTPException`
  details already keep server-side-only (Stage 24 §10's convention,
  unchanged).

**Consequence for the UI, not a gap to fix here:** the backend has no
streaming/WebSocket transport (§14) — `POST /approve` is one blocking
call that runs the *entire* subtask loop before returning. The trace can
only ever be rendered **after** `/approve` resolves, as a completed
record, never as a live step-by-step feed. §7 designs the trace panel
around that constraint rather than pretending otherwise.

### 3.3 CORS policy for a browser-based caller — still open (§15)

Every existing caller (`curl`, `TestClient`) is same-origin or
origin-agnostic. A browser enforces CORS; Vite's dev server (default
`http://localhost:5173`) calling FastAPI (`http://127.0.0.1:8000`) is a
cross-origin request and will be blocked without an explicit policy.

**Proposed addition:** `fastapi.middleware.cors.CORSMiddleware`,
allow-listing only the known dev/build origin(s) (e.g.
`http://localhost:5173`), not `allow_origins=["*"]` — consistent with
this stage's "don't weaken existing guardrails" instruction; a wildcard
origin would undermine Stage 24's other protections for no reason. Exact
origin(s) to allow-list is an implementation-time detail (§15) depending
on how the app is served in dev vs. any future deployment.

---

## 4. Backend Integration Contract

The full contract the React app is built against — Stage 24's seven
existing routes, unchanged, plus the two confirmed additions from §3.1
and §3.2. Every request/response shape below is copied from
`stage24_security_guardrails/main.py`'s actual Pydantic models (or, for
the two additions, the confirmed shape defined in §3), not approximated.

### 4.1 Endpoint summary

| Method | Path | Purpose | Stage 25 addition? |
|---|---|---|---|
| GET | `/health` | Backend/DB reachability check | No |
| POST | `/chat` | Start a research question on a thread | No |
| POST | `/approve` | Resume a paused thread, run research | No (response gains `trace`, §3.2, confirmed) |
| POST | `/reject` | Resume a paused thread, decline the plan | No |
| POST | `/documents/upload` | Upload a PDF/TXT/DOCX | No |
| POST | `/documents/search` | Semantic search (not used for listing — see §3.1) | No |
| POST | `/documents/backfill-embeddings` | Maintenance-only; **not called by the UI** | No |
| GET | `/documents` | List a user's uploaded documents | **Yes — confirmed (§3.1)** |

### 4.2 `POST /chat`

```ts
// Request
{ question: string; thread_id: string; user_id: string }

// Response (always this shape from /chat, per the graph's fixed shape -
// human_approval() unconditionally interrupts)
{
  thread_id: string;
  status: "awaiting_approval";
  subtasks: string[];
  approval_prompt: string;   // e.g. "Approve this plan? (y/n): "
}
```

Errors: `400` empty/overlong `question`/`thread_id`/`user_id`
(`{"detail": "..."}`, static string per Stage 24 §10), `413` request body
too large, `429` rate limited, `409` thread busy (concurrent request on
the same `thread_id`), `500` generic failure. See §6 for how each is
surfaced.

### 4.3 `POST /approve`

```ts
// Request
{ thread_id: string }

// Response
{
  thread_id: string;
  status: "completed";
  subtasks: string[];
  results: string[];        // one final answer per subtask, same order
  final_answer: string;     // synthesized combined answer
  trace: SubtaskTrace[];    // confirmed, §3.2 - one entry per subtask
}

interface SubtaskTrace {
  subtask: string;
  specialist: "research" | "knowledge" | "analysis";  // agent name
  tools_used: string[];      // tool name(s), e.g. ["search_uploaded_documents"]
  status: "completed";       // execution status (§3.2)
  verdict: "pass" | "retry"; // high-level result
  retry_count: number;
}
```

Errors: `404` unknown `thread_id`, `409` not currently awaiting approval
(or thread busy), `500` generic failure. This call can take real wall-
clock time (the full supervisor → specialist → critic loop, per subtask,
with possible retries) — the UI must show a busy/loading state for the
whole duration (§6, §7).

### 4.4 `POST /reject`

```ts
// Request
{ thread_id: string }
// Response
{ thread_id: string; status: "rejected"; subtasks: string[]; results: []; final_answer: "" }
```

Same error cases as `/approve`.

### 4.5 `POST /documents/upload`

`multipart/form-data`, not JSON:

```ts
// Request (FormData)
file: File          // .pdf / .txt / .docx only
user_id: string

// Response
{
  document_id: string;
  filename: string;
  file_type: string;
  user_id: string;
  chunk_count: number;
  status: "stored";
}
```

Errors (all with a static `detail` string — see Stage 24 §10's table,
unchanged): `400` bad `user_id`/filename-too-long/unsupported type, `413`
file too large, `422` corrupted/empty-text/dangerous file (PDF page cap,
DOCX zip-bomb cap, extraction timeout — all indistinguishable by design,
§6), `429` rate limited, `500` generic failure.

### 4.6 `GET /documents` (confirmed addition, §3.1)

```ts
// Request
GET /documents?user_id={user_id}

// Response
{
  documents: [
    { document_id: string; filename: string; file_type: string; chunk_count: number; created_at: string }
  ]
}
```

Errors: `400` empty `user_id`, `429` rate limited. An empty `documents`
array is a valid `200`, not an error (§7, §13 — the empty-state case).

### 4.7 `POST /documents/search`

Used only if a future within-Stage-25 feature wants raw chunk-level
search (not required by any of the ten UI requirements — the chat
interface goes through `/chat` → `/approve`, which internally calls the
Knowledge Agent, not this route directly). Documented for completeness,
not wired into any planned Stage 25 component. See §14.

### 4.8 Error handling, uniformly

Every error response is `{"detail": "<short static string>"}` — Stage
24's own convention (§10 there), unchanged. The React API client (§9)
has exactly **one** place that parses an error response and extracts
`detail`; every component that surfaces an error displays that string
verbatim, and only that string — never the HTTP status text, never a
caught JS exception's `.message`, never a raw response body. This is
what "don't duplicate security logic, never expose internals" (§8) means
concretely for error display: the backend already decided what's safe to
show; the frontend's job is to relay it faithfully, not to add detail
that wasn't there.

### 4.9 How approval/resume works, end to end

```
1. User types a question, clicks Send.
2. React generates/reuses a thread_id (§8), calls POST /chat.
3. /chat ALWAYS returns status: "awaiting_approval" (the graph's
   human_approval() node unconditionally interrupts) - the UI shows the
   plan (subtasks) and an Approve/Reject control. No polling: this is a
   direct call/response, not a background job.
4. User clicks Approve -> POST /approve {thread_id}.
   This is a SYNCHRONOUS, potentially slow call (multiple LLM calls
   across up to 3 subtasks, each possibly retried once) - the UI shows a
   busy state for the whole duration, not a spinner that lies about
   progress.
5. /approve returns status: "completed" with results, final_answer, and
   trace (§3.2). UI renders the final answer in chat and the trace in
   the execution panel.
   OR: User clicks Reject -> POST /reject {thread_id} -> status:
   "rejected", no research ran, UI shows that plainly (not as an error).
```

---

## 5. Document Upload

- A dedicated upload control (drag-and-drop target + a plain file-picker
  fallback, since drag-and-drop alone isn't reliably discoverable or
  accessible) accepting `.pdf`/`.txt`/`.docx` — the `accept` attribute is
  a UX hint only; the backend remains the real gate (§8).
- Client-side pre-check before the request even fires: file extension
  and a size ceiling mirroring `MAX_FILE_SIZE_BYTES` (20 MB, a value the
  UI reads from a shared constant, not a guess) — purely to fail fast
  with a friendly message instead of waiting on a round trip for an
  obviously-oversized file. This is UX, not security (§8) — the backend
  check is authoritative regardless of what the client believes about a
  file.
- Upload progress: `fetch`'s body-upload progress isn't observable
  without `XMLHttpRequest` or a streaming body; given this project's
  "no new dependency where standard tools suffice" ethos, the plan should
  default to an indeterminate "uploading…" state (spinner, not a percent
  bar) unless the implementation plan specifically justifies adding
  `XMLHttpRequest`-based progress tracking. Flagged for the plan, not
  decided here (§15).
- On success: the new document appears in the sidebar list (re-fetch
  `GET /documents`, or optimistically prepend the `UploadResponse` shape
  translated into a `DocumentSummary` — implementation choice, §15) with
  its filename, type, and chunk count.
- On failure: the exact backend `detail` string (§4.8) shown inline near
  the upload control, file-picker/drop-zone reset so the user can retry
  immediately.
- The document list (`GET /documents`, §3.1/§4.6) is fetched once on
  load (for the current identity, §8) and refreshed after every
  successful upload. No polling.

---

## 6. Chat Interface

- A message list showing the running conversation for the current
  `thread_id`: the user's question, then (after approval) the assistant's
  `final_answer`. Per-subtask `results` are available for the trace panel
  (§7), not duplicated into the main chat thread as separate messages —
  the chat area is for the question and the one synthesized answer per
  turn, matching what a user actually asked.
- Loading states, one per phase (§4.9) — these are genuinely different
  waits and should look different to the user, not one generic spinner:
  - "Planning…" while `POST /chat` is in flight.
  - "Awaiting your approval" once the plan arrives (not a loading state —
    an actionable one, §7).
  - "Researching…" while `POST /approve` is in flight (this is the long
    one; the UI should say so, e.g. "This can take a little while").
- Error states: any `4xx`/`5xx` from `/chat` or `/approve` renders the
  backend's `detail` string (§4.8) as an inline error in the chat area,
  with a way to retry the same question (re-submit `/chat` — a `409`
  "thread busy" in particular is expected to be transient and worth an
  explicit "try again" affordance, not treated as fatal).
- Conversation/thread handling: one `thread_id` per conversation, created
  client-side (§8) when the user starts a new chat; follow-up questions
  in the same conversation reuse it. Starting a "new chat" in the UI
  generates a fresh `thread_id` and clears the visible message list —
  the backend has no delete/reset call to make (checkpoints for an old
  `thread_id` simply stop being visited).
- The input control is disabled while a `/chat` or `/approve` call is in
  flight, and while a plan is awaiting approval (the next real action is
  Approve/Reject, not another question) — preventing a second `/chat` on
  the same `thread_id` mid-flight, which would otherwise race the
  in-flight one (Stage 24's `_thread_lock` would correctly serialize
  them, but there's no reason to make the user wait on a `409` when the
  UI can simply not offer the action yet).

---

## 7. Agent Execution Trace

Built entirely from `ThreadStatusResponse.trace` (§3.2/§4.3) — available
only once `/approve` returns `status: "completed"`. Given §3.2's
consequence, this is a **panel that populates once, after the fact**, not
a live feed:

- One entry per subtask, in order: the subtask text, which specialist
  handled it (`research` / `knowledge` / `analysis` — rendered with a
  clear, distinct label/icon per specialist, not raw enum text), which
  tool(s) it invoked (by name — e.g. `search_uploaded_documents`,
  `duckduckgo_search`, `calculate` — or "no tool used" if the specialist
  answered directly), and the critic's verdict (`pass` immediately, or
  `retry` — shown as "needed one retry" when `retry_count > 0`, not as a
  failure/error state; a bounded retry succeeding is the system working
  as designed, per Stage 14's own critic design).
- Explicitly, visibly separate from the final answer in the chat area
  (§6) — a distinct panel (§11), never interleaved into the same message
  bubble, so "what the system did" and "what it told you" are never
  visually conflated.
- Never renders anything excluded in §3.2: no system prompt text, no raw
  tool arguments/outputs, no critic feedback text, nothing from an error
  response body beyond its `detail` string.
- Empty/absent state: before a conversation has completed at least once
  (no thread yet, or still awaiting approval), the panel shows a plain
  empty state ("Execution details will appear here once a question is
  answered"), not a blank area that looks broken.

---

## 8. Human Approval

- Once `/chat` returns `status: "awaiting_approval"`, the UI renders the
  plan (`subtasks`, as a numbered list) and the `approval_prompt` text
  verbatim, with two explicit controls: **Approve** and **Reject** — not
  a single toggle, not a text input asking the user to type y/n (that
  mirrors the REPL's UX, not a GUI's).
- **Approve** calls `POST /approve {thread_id}` (§4.3); **Reject** calls
  `POST /reject {thread_id}` (§4.4). Both controls are disabled the
  instant either is clicked (no double-submit), and both fire the
  matching loading state from §6.
- A rejected plan is shown plainly in the chat area (e.g. "Plan declined
  — no research was run"), not as an error — `reject` is a normal,
  successful outcome of this flow, and Stage 24's `ThreadStatusResponse`
  already distinguishes `"rejected"` from any failure status.
- No other UI surface can resume a paused thread — approval is only ever
  reachable through this one panel, tied to the specific `thread_id` that
  produced the `awaiting_approval` state currently on screen. The UI
  never lets a user "approve" a thread it didn't itself just receive an
  `approval_prompt` for.

---

## 9. User/Document Isolation

Stage 23's isolation guarantee (`documents.user_id`, filtered on every
retrieval path, never bypassable by a claimed `user_id` reading someone
else's data) is enforced entirely **server-side**, already, and Stage 25
changes nothing about that mechanism. The React app's only
responsibility is to **participate in it honestly**:

- Every request that takes a `user_id` (`/chat`, `/documents/upload`,
  `/documents/search`, and new `GET /documents`, §3.1) sends the current
  session's identity, sourced from exactly one place in the app's state
  (§10) — never a value read from a URL parameter, a hidden form field a
  user could tamper with more easily than the real one, or anything
  else that could drift from what's actually being used.
- The UI never merges, caches, or displays document/trace data for more
  than one `user_id` at a time. Switching identity (§10) clears the
  document list, the chat thread, and the trace panel and re-fetches
  fresh — it does not attempt to "keep both around."
- **The client cannot bypass backend permissions, because it is never
  given a code path that could** — there is no client-side filter being
  relied on to hide another user's data that the backend already sent;
  the backend never sends it in the first place (Stage 23's guarantee).
  The frontend has nothing to get wrong here beyond "send the right
  `user_id`, consistently."
- **Identity model, since there is no authentication (§14):** matching
  Stage 23's existing trust model exactly (`user_id` is a plain,
  self-asserted string, same as `thread_id` always has been), the UI asks
  for a display name / `user_id` once (a simple text prompt on first
  load), persists it in `localStorage` for that browser, and shows it
  somewhere always-visible (§11) so it's never ambiguous which identity
  is active. This is **not** a login and must not be presented as one —
  no password field, no "sign in" language — it is exactly as much
  identity as `thread_id` already provides, made visible instead of
  buried in a request body.

---

## 10. Security

React's role here is narrower than the backend's, by design (§1, §9):

- **No security logic is duplicated in React that the backend already
  enforces.** Client-side file-type/size checks (§5) and input-length
  hints are UX conveniences that fail fast with a friendlier message —
  never the actual gate, and never presented to the user as if they were
  ("this file looks too large" phrased as a warning, not "rejected").
  Every one of Stage 24's real guardrails (file validation, dangerous-
  file handling, prompt-injection defense, the leak guard, rate
  limiting) lives exactly where it already lives: the backend. Nothing
  in this spec re-implements any of them client-side.
- **No secrets in the frontend, ever.** `OPENAI_API_KEY` and
  `DATABASE_URL` are backend-only and never referenced by, bundled into,
  or fetched by the React app. The only configuration the frontend needs
  is the backend's base URL (§12, an env var read at build/dev time, not
  a secret).
- **Errors relay the backend's `detail` string only** (§4.8) — never a
  caught exception's raw message, never a full response body dumped to
  the UI, never anything from the browser console surfaced to the page.
- **The trace panel (§7) is held to the same "no internals" bar as the
  backend's own error handling** — it renders only the fields §3.2
  explicitly allows (specialist name, tool names, execution status,
  verdict, retry count), sourced from a typed response (§12), not from
  freeform text that could accidentally carry more than intended. No
  system prompt, no API key, no credential, no raw tool output ever has
  a field on `SubtaskTrace` to travel through in the first place (§3.2).
- **CORS (§3.3)** is the one place a browser-specific security concern
  is genuinely new in this stage; it's a backend allow-list addition, not
  frontend code, and is scoped to the known dev origin, not `"*"`.

---

## 11. UX and Layout

A single-page layout, three panels plus a header — no client-side router
needed (§12) for one screen:

```
+----------------------------------------------------------------+
| Header: app name | current identity (§9) [change]              |
+----------------+----------------------------+------------------+
| Document        | Chat area                  | Execution trace  |
| sidebar         |                             | panel            |
|                 | - message list (§6)         |                  |
| - upload control| - awaiting-approval panel   | - per-subtask    |
|   (§5)          |   (§8), when applicable     |   entries (§7)   |
| - document list |                             | - empty state    |
|   (§4.6)        | - chat input (disabled per  |   until a turn   |
| - empty state   |   §6's rules)               |   completes      |
|   ("No documents|                             |                  |
|   yet")         |                             |                  |
+----------------+----------------------------+------------------+
```

- **Responsive**: on a narrow viewport, the sidebar and trace panel
  collapse to togglable drawers/tabs rather than being squeezed
  side-by-side with the chat area — the chat area is the primary surface
  and always gets priority width.
- **Empty states**, each distinct and named explicitly so none is left
  as an accidental blank screen: no documents yet (§5); no conversation
  started yet (chat area, before the first question); trace panel before
  any turn has completed (§7).
- **Loading states** are per §6 — distinct copy per phase, not one
  generic spinner reused everywhere.
- **Error states** always show the backend's exact `detail` text (§4.8,
  §10), styled distinctly from normal content (e.g. a consistent inline
  error treatment), and never block the rest of the UI from being usable
  (a failed upload doesn't freeze the chat area, a failed chat message
  doesn't freeze the sidebar).
- Visual design: a "clean modern chat application" look is a design
  requirement, not a technical one — left to the implementation plan to
  propose concretely (colors, spacing, typography), but built on
  whichever styling approach §12 settles on, applied consistently rather
  than inlined ad hoc per component.

---

## 12. Project Structure

```
stage25_react_ui/
├── backend/
│   └── main.py          # Stage 24's main.py + §3.1/§3.2 (confirmed) + §3.3 (CORS)
├── README.md             # this stage's README, per CLAUDE.md's convention
├── package.json
├── tsconfig.json
├── vite.config.ts
├── .env.example           # VITE_API_BASE_URL=http://127.0.0.1:8000
├── public/
└── src/
    ├── main.tsx
    ├── App.tsx                     # top-level layout (§11), no router
    ├── api/
    │   ├── client.ts               # one fetch wrapper; the ONE place
    │   │                           # that parses `detail` (§4.8, §10)
    │   ├── chat.ts                 # postChat, postApprove, postReject
    │   ├── documents.ts            # uploadDocument, listDocuments
    │   └── types.ts                # mirrors the backend Pydantic models
    │                                 exactly (§4) - ChatRequest,
    │                                 ThreadStatusResponse, SubtaskTrace,
    │                                 UploadResponse, DocumentSummary, ...
    ├── state/
    │   └── AppContext.tsx          # current identity (§9), active
    │                                 thread_id + messages, document list,
    │                                 trace, in-flight/error state per
    │                                 phase (§6) - React Context + hooks,
    │                                 no external state library (matches
    │                                 this project's "no dependency where
    │                                 the standard tool suffices" ethos)
    ├── hooks/
    │   ├── useChat.ts              # wraps /chat + /approve + /reject
    │   │                             flow and its loading/error phases
    │   ├── useDocuments.ts         # wraps GET /documents + upload
    │   └── useIdentity.ts          # reads/writes the localStorage
    │                                 identity (§9)
    ├── components/
    │   ├── layout/
    │   │   ├── Header.tsx          # app name + identity display (§9, §11)
    │   │   └── AppLayout.tsx       # the three-panel/responsive shell (§11)
    │   ├── documents/
    │   │   ├── DocumentSidebar.tsx
    │   │   ├── DocumentUploader.tsx    # drag-drop + file picker (§5)
    │   │   ├── DocumentListItem.tsx
    │   │   └── DocumentEmptyState.tsx
    │   ├── chat/
    │   │   ├── ChatArea.tsx
    │   │   ├── MessageList.tsx
    │   │   ├── MessageBubble.tsx
    │   │   ├── ChatInput.tsx
    │   │   ├── ApprovalPanel.tsx       # plan + Approve/Reject (§8)
    │   │   └── ChatEmptyState.tsx
    │   ├── trace/
    │   │   ├── ExecutionTracePanel.tsx  # §7
    │   │   ├── SubtaskTraceEntry.tsx
    │   │   └── TraceEmptyState.tsx
    │   └── common/
    │       ├── ErrorBanner.tsx     # renders `detail` only (§4.8, §10)
    │       ├── LoadingIndicator.tsx    # takes a phase label (§6)
    │       └── IdentityPrompt.tsx  # first-load "who are you" (§9)
    └── styles/
        └── ...                     # approach TBD, §15
```

- **State management**: React Context + hooks (`AppContext.tsx` +
  `use*` hooks above), not Redux/Zustand/another library — this app has
  one small, mostly-linear piece of shared state (identity, one active
  thread, one document list, one trace), which doesn't need a dedicated
  state-management dependency to manage cleanly.
- **Types**: every request/response shape in `api/types.ts` is a direct,
  field-for-field mirror of the Pydantic models in §4 — including the
  new `DocumentSummary`/`DocumentListResponse`/`SubtaskTrace` from §3.
  Nothing is typed as `any`; a response the client doesn't recognize is a
  bug to surface, not to silently accept.
- **API client**: one `fetch`-based module (`api/client.ts`) that every
  other `api/*.ts` file goes through — no `axios` or other HTTP library
  dependency, matching this project's minimal-dependency convention
  established since Stage 1.
- **Styling approach**: left as an open decision for the implementation
  plan (§15) — CSS Modules (zero extra dependency, scoped by default) is
  the default recommendation; Tailwind is a reasonable alternative if a
  "clean modern" look is wanted faster, at the cost of a real dependency
  this project hasn't taken on anywhere else yet.

---

## 13. Acceptance Criteria

Restating the request's list as concrete, checkable outcomes, each tied
to the section that defines how it's built:

| # | Criterion | Built by |
|---|---|---|
| 1 | A user can upload a document | §5, `POST /documents/upload` (§4.5) |
| 2 | The document appears in the UI | §5, `GET /documents` (§3.1/§4.6) |
| 3 | The user can ask a question | §6, `POST /chat` (§4.2) |
| 4 | The Knowledge Agent searches only uploaded documents | Unchanged backend behavior (Stage 22/23) - the UI does nothing to affect this; verified by the trace panel (§7) showing `specialist: "knowledge"` + `tools_used: ["search_uploaded_documents"]` when a document question is asked |
| 5 | The answer appears in the chat | §6, `final_answer` from `POST /approve` (§4.3) |
| 6 | Agent/tool execution is visible | §7, `trace` (§3.2/§4.3) |
| 7 | Human approval works | §8, `POST /approve`/`POST /reject` (§4.3/§4.4) |
| 8 | Errors are displayed cleanly | §6, §10, §11 - every error path renders the backend's `detail` string, styled distinctly, never blocking the rest of the app |

Criteria 2 and 6 depend directly on §3.1/§3.2, both now confirmed;
criterion 4 depends on §3.2's trace data to be *observable*, though the
underlying isolation/routing behavior it checks is unchanged Stage 22/23
behavior either way. All three remain implementation work, not open
design questions, as of this revision.

---

## 14. Explicitly Out of Scope

- **Authentication of any kind.** Per explicit instruction and consistent
  with Stage 23/24's existing trust model — the identity prompt (§9) is
  not a login, has no password, and verifies nothing.
- **Any change to the LangGraph architecture** — node/edge shape,
  supervisor routing logic, critic behavior, retry limits, subgraph
  composition. Stage 25 only ever adds a new route (§3.1), a new response
  field capturing already-computed data (§3.2), and CORS middleware
  (§3.3) — no node, edge, or prompt in the graph itself changes.
- **New agents, tools, or RAG systems.** Research, Knowledge, and
  Analysis stay exactly as Stage 24 left them; no fourth specialist, no
  new tool, no second retrieval source.
- **Streaming/WebSocket transport.** `/chat` and `/approve` stay
  synchronous request/response, exactly as Stage 19-24 built them; the
  trace panel (§7) is designed around that constraint rather than this
  stage silently adding real-time infrastructure to work around it. A
  streaming upgrade, if ever wanted, is a separate future stage.
- **Wiring `POST /documents/search` into any UI component.** Documented
  in §4.7 for completeness only; no requirement in this spec needs raw
  chunk-level search exposed to the user.
- **`POST /documents/backfill-embeddings` in the UI.** A maintenance
  operation (Stage 21 onward); no end-user surface calls it.
- **Any production deployment concern** — hosting, HTTPS/TLS, a build
  pipeline beyond `vite build`, environment-specific secrets management.
  This spec covers a local-dev-oriented app talking to a local backend,
  matching every prior stage's "how to run" scope.
- **Editing or deleting an uploaded document, or renaming/deleting a
  conversation.** No backend route exists for either today, and neither
  is one of the ten requested capabilities — noted as a real, related,
  unaddressed gap for a future stage, not silently ignored.
- **Multi-conversation history / a list of past threads.** The backend
  has no way to enumerate a user's `thread_id`s (Stage 23 §12 already
  flagged thread-level ownership as a separate, unaddressed gap); Stage
  25's "new chat" (§6) generates a fresh `thread_id` but does not persist
  or list prior ones.

---

## 15. Open Decisions to Confirm at Implementation Time

- **CORS origin(s) to allow-list (§3.3)** — the one backend-shape
  decision still open; `GET /documents` and the `trace` field (§3.1,
  §3.2) are confirmed and no longer open.
- Exact folder name (`stage25_react_ui` proposed, following the
  `stageN_<topic>` convention) and whether the backend delta (§3) lives
  at `stage25_react_ui/backend/main.py` as proposed, or elsewhere.
- Styling approach (§12): CSS Modules (default recommendation) vs.
  Tailwind vs. another minimal option.
- Upload progress UX (§5): indeterminate spinner (default recommendation)
  vs. a real percent bar via `XMLHttpRequest`.
- Whether the document list refreshes via re-fetching `GET /documents`
  after upload, or is updated optimistically from the `UploadResponse`
  shape directly (§5) — a minor implementation choice with no behavioral
  difference to the user.
- Exact rate-limit numbers for the new `GET /documents` route (§3.1) -
  proposed to reuse a similar shape to Stage 24's existing per-route
  limits, exact figures left open.
- Whether `PlannerState.results` becomes a list of a new structured type
  or gains a parallel `list[SubtaskTrace]` field (§3.2) - an
  implementation detail with no behavioral difference to the API
  contract in §4.3.
