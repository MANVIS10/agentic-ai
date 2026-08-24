# Stage 23 Specification — Per-User Document Isolation

## 0. Status

```text
Stage 20  Document upload/ingest         ✅ (deliberate extension, post-roadmap)
Stage 21  Embeddings + vector search     ✅ (deliberate extension, post-roadmap)
Stage 22  Knowledge Agent RAG            ✅ (deliberate extension, post-roadmap)
Stage 23  Per-user document isolation    → Next (this spec)
```

Like Stage 18-22, Stage 23 is a deliberate extension past the original
roadmap in `spec_document.md`, not a numbered item from it. It builds on
Stage 22's Knowledge Agent (`stage22_knowledge_agent_rag/`) the same way
every prior stage built on the one before it: a new stage folder that
duplicates what it needs rather than editing the previous stage in place,
per `CLAUDE.md`'s "previous stages left untouched, no shared `common/`
module" rule.

This document is a **specification only**. No implementation code is
written against it yet.

---

## 1. Purpose

Every stage from 20 through 22 stored and searched uploaded documents in a
single shared pool: `documents`/`document_chunks` have no notion of who
uploaded what, and `search_uploaded_documents` (Stage 22 §4) queries
across every row in the database with no filter narrower than "has an
embedding." Stage 20 §9 explicitly deferred this ("documents are not
scoped to a user or account, only to their own `document_id`"), and Stage
22 §9 explicitly deferred it again ("there is no concept yet of ... 'this
user's documents only'"). That was the right call for those stages — one
concept at a time — but it means the system today has a real problem:
**anyone who asks the Knowledge Agent a question gets answers drawn from
every document anyone has ever uploaded.** There is no accounts system in
this project (still true after this stage — see §12), but the absence of
*isolation* is a distinct gap from the absence of *authentication*, and
this stage closes the isolation gap only.

Concretely: if User A uploads a document containing sensitive or simply
private content, and User B later asks the Knowledge Agent an unrelated-
looking question that happens to be semantically close to User A's
content, User B's answer today can quote User A's document back to them.
Stage 23 makes that impossible by attaching an owner to every document at
upload time and filtering every retrieval path — the HTTP search endpoint
and the Knowledge Agent's in-process tool alike — by that owner before any
chunk content is allowed to reach a response.

This is deliberately narrow: it does not add accounts, passwords, tokens,
or sessions (§12). It adds the minimum data-model and query changes needed
so that **no code path in this project can return chunk content belonging
to a `user_id` other than the one the caller asserts.**

---

## 2. Scope of Stage 23

In scope:

- A `user_id` owner attached to every document at upload time.
- A required, explicit way for every document-touching request to state
  which user it's acting as (§3).
- Filtering `POST /documents/search` and the Knowledge Agent's
  `search_uploaded_documents` tool by that `user_id`, so neither can ever
  return another user's chunks (§6).
- Threading `user_id` through the planner → supervisor/critic →
  Knowledge Agent subgraph chain so the tool call has it available without
  exposing it to the LLM as a controllable argument (§7).
- A backward-compatible migration for `documents` rows written by Stage
  20-22 before `user_id` existed (§5).
- Tests that positively demonstrate cross-user retrieval is impossible,
  not just that the isolated case looks right in isolation (§11).

Explicitly not in scope — see §12.

---

## 3. User Identification Approach

**This is not authentication.** There is no login, no password, no token,
and no server-side registry of valid users anywhere in this project
(Stage 19-22 have none either — every existing endpoint is open). Adding
real auth would be a much larger, separate concept than "isolate documents
by owner," and this project's stages build one concept at a time
(`CLAUDE.md`). Stage 23 instead uses the same trust model this project
already relies on for `thread_id`: **a plain, caller-supplied string that
the API trusts at face value.** Nothing in this stage verifies that the
caller "is" the `user_id` it claims — the guarantee this stage provides is
narrower and still meaningful: *given* a `user_id`, the system will never
hand back another `user_id`'s document content. Whether the caller is
honest about which `user_id` it is remains outside this stage's boundary
(§10 makes this explicit as a security boundary, not a hidden gap).

**Where `user_id` is supplied**, matching this project's existing
convention of explicit Pydantic/form fields (no header-based conventions
exist anywhere in Stage 19-22, so a new custom header would be a new,
unprecedented pattern rather than the minimum change):

| Endpoint | Field | Shape |
|---|---|---|
| `POST /documents/upload` | `user_id` | new required `Form(...)` field, alongside the existing `file` field (multipart, like `filename`/`file_type` already are) |
| `POST /documents/search` | `user_id` | new required field on the existing `SearchRequest` JSON body |
| `POST /chat` | `user_id` | new required field on the existing `ChatRequest` JSON body |

`POST /approve` and `POST /reject` **do not** gain a `user_id` field. They
resume an already-started thread by `thread_id`, and `user_id` is captured
once at `/chat` time into the planner's checkpointed state (§7) — it does
not need to be resupplied, the same way `question` isn't resupplied to
`/approve` either.

**Validation**: `user_id` must be a non-empty string after `.strip()`.
Empty/whitespace-only is rejected the same way `POST /documents/search`
already rejects an empty `query` (§10). No format, length, or allow-list
constraint beyond that — exactly as permissive as `thread_id` already is
today, since inventing a stricter rule for `user_id` alone would be
inventing a requirement this project has never had for any other
caller-supplied identifier.

---

## 4. Why Not Header-Based or Auth-Based Identification

Considered and rejected, for the record:

- **A custom header (`X-User-Id`)** would centralize the field into one
  FastAPI dependency instead of three schema edits, but it introduces a
  pattern (headers as a source of application data) this project has never
  used — every existing route reads exclusively from the body/form. Three
  small, explicit field additions are more consistent with how Stage
  19-22 already read, and more visible to a beginner reading the route
  signature.
- **Real authentication** (API keys, JWT, sessions) would correctly solve
  "is the caller telling the truth about `user_id`," but that is a
  meaningfully bigger, separate concept than data isolation, and nothing
  in Stage 1-22 has built any authentication primitive to build on. Per
  `CLAUDE.md`'s "one major concept at a time," it is deferred (§12), not
  quietly bundled in.

---

## 5. Data Model Changes

### `documents` — one new column

```sql
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default-user';

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents (user_id);
```

Same idiom Stage 21 §5 established for evolving an existing table
(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, run unconditionally at module
load, safe on every process start) rather than a migrations framework,
per this project's minimal-dependencies rule. Two things to note:

- **`NOT NULL DEFAULT 'default-user'`, not a nullable column.** Because
  Stage 18-22 all share the one running Postgres instance/volume
  (`docker-compose.yml`, per Stage 21 §6), this table already has rows
  from Stage 20-22 testing with no concept of an owner. Postgres backfills
  every pre-existing row with the literal string `'default-user'` as part
  of running this `ALTER TABLE`, and enforces `NOT NULL` for everything
  from then on — so there is never a row with an unowned/`NULL` document,
  which would otherwise be a special case every filtering query (§6) would
  need to account for. New uploads always supply a real `user_id`
  explicitly (§3); the `DEFAULT` only ever fires during this one
  migration statement.
- **Index, not just a column.** `user_id` becomes a filter condition on
  every retrieval query from this stage forward (§6), so a plain B-tree
  index on it is standard, cheap, and not the kind of premature
  optimization Stage 21 §13 deferred for vector indexing (`ivfflat`/
  `hnsw` tune an approximate-nearest-neighbor structure at real cost and
  complexity; a B-tree on an equality-filtered text column is neither).

### `document_chunks` — unchanged

No new column here. `user_id` lives on `documents` only; every query that
needs to filter by it already joins `document_chunks` to `documents` for
`filename` (Stage 21 §7's search query, Stage 22 §4's tool query), so
`AND d.user_id = %s` is an addition to an existing join condition, not a
new join. Denormalizing `user_id` onto `document_chunks` directly would
save one join per query at the cost of a second copy of the same fact
that could drift out of sync — not worth it at this project's scale
(Stage 21 §5 made the same call about not adding a redundant
`chunk_embeddings` table).

### Relationship diagram (updated from Stage 20 §4 / Stage 21 §9)

```text
documents (owner: user_id)
     │  1
     │
     │  N
document_chunks (owner: inherited via documents.user_id, via JOIN)
```

---

## 6. Search Filtering Requirements

**Rule, stated once, that both retrieval paths below must satisfy
identically: no query may return a `document_chunks` row whose parent
`documents.user_id` does not equal the requesting `user_id`.** Both paths
share the same underlying SQL shape (Stage 22 §4 already notes the tool
"runs Stage 21's existing cosine-similarity SQL" in-process rather than
duplicating it conceptually), so the filter is described once here and
applies to both.

### `POST /documents/search` (Stage 21 §7, Stage 22's copy)

Add `d.user_id = %(user_id)s` to the query's `WHERE` clause (the same
inner-subquery shape Stage 22's implementation already uses to compute
`similarity` once and reuse it in both `WHERE` and `ORDER BY`):

```sql
SELECT * FROM (
    SELECT dc.id AS chunk_id, dc.document_id, dc.chunk_index, dc.content,
           d.filename, 1 - (dc.embedding <=> %s) AS similarity
    FROM document_chunks dc
    JOIN documents d ON d.id = dc.document_id
    WHERE dc.embedding IS NOT NULL
      AND d.user_id = %s
      -- existing optional "AND dc.document_id = %s" stays, unchanged
) sub
WHERE similarity >= %s   -- existing optional threshold filter, unchanged
ORDER BY similarity DESC LIMIT %s
```

**`document_id` scoping interacts with ownership**: today, when a caller
passes `document_id`, the route first checks `SELECT 1 FROM documents
WHERE id = %s` and 404s if that document doesn't exist. That existence
check must now also require ownership:

```sql
SELECT 1 FROM documents WHERE id = %s AND user_id = %s
```

A `document_id` that exists but belongs to a *different* `user_id` must
produce the **exact same response** as a `document_id` that doesn't exist
at all — `404`, `"No document found for this document_id"` — never a
different message, status code, or any detail that would let a caller
distinguish "doesn't exist" from "exists but isn't yours." That
distinction is itself information about another user's data (§8).

### `search_uploaded_documents` tool (Stage 22 §4)

Same added condition, same join, applied to the tool's own copy of the
query:

```sql
SELECT dc.content, d.filename
FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
WHERE dc.embedding IS NOT NULL
  AND d.user_id = %s
ORDER BY dc.embedding <=> %s
LIMIT %s
```

The tool has no `document_id` parameter (Stage 22 §4 already decided
against exposing that to the LLM), so only the ownership filter is new
here — no interaction with a second scoping parameter to reason about.

---

## 7. Threading `user_id` Through the Graph (Knowledge Agent Tool Access)

The tool's `user_id` must come from **trusted server-side context**, not
from an LLM-controllable argument — the same reasoning Stage 22 §9 already
applied to reject exposing `document_id`/threshold to the model ("The tool
takes a single `query: str` argument"). A `user_id: str` parameter on the
tool's own signature would let a compromised or confused LLM call
`search_uploaded_documents(query=..., user_id="someone-else")` and read
another user's documents through prompt injection alone — exactly the
vulnerability this stage exists to close. So `user_id` must reach the
tool through a channel the model cannot write to.

**Mechanism: LangGraph's `InjectedState`.** The Knowledge Agent subgraph's
state schema gains a `user_id` field, and the tool declares it as an
injected parameter instead of a normal one:

```python
from langgraph.prebuilt import InjectedState

class KnowledgeState(MessagesState):
    user_id: str

@tool
def search_uploaded_documents(
    query: str,
    user_id: Annotated[str, InjectedState("user_id")],
) -> str:
    ...
```

`ToolNode` populates `user_id` from the graph state when it executes the
tool call; `bind_tools` excludes `InjectedState`-annotated parameters from
the schema shown to the LLM, so the model only ever sees and controls
`query` — identical to today's tool-calling shape from the model's point
of view. `knowledge_subgraph_builder = StateGraph(KnowledgeState)` replaces
today's `StateGraph(MessagesState)` for this one subgraph only; Research
and Analysis subgraphs are untouched (neither touches documents).

**Threading `user_id` down from the HTTP layer to that subgraph:**

```text
POST /chat  (user_id: new required field, §3)
     │
     ▼
graph.invoke({"question": ..., "user_id": ...}, config)   ← PlannerState gains user_id
     │
     ▼  (checkpointed by PostgresSaver — survives the human_approval interrupt/resume,
     │   so /approve needs no user_id of its own; the value set at /chat time is what
     │   research_subtask still sees after resume)
     ▼
research_subtask()
     │  supervisor_critic_graph.invoke({"messages": ..., "user_id": state["user_id"]})
     ▼                                                      ← CriticState gains user_id
knowledge_node()
     │  knowledge_graph.invoke({"messages": ..., "user_id": state["user_id"]})
     ▼                                                      ← KnowledgeState (above)
knowledge_agent_node() → ToolNode(knowledge_tools)
     │  search_uploaded_documents(query=..., user_id=<injected>)
     ▼
Filtered by documents.user_id (§6)
```

Only three state schemas change, and only by adding one field each:
`PlannerState`, `CriticState`, `KnowledgeState` (new, replacing
`MessagesState` for the knowledge subgraph specifically). `research_node`
and `analysis_node` do not need `user_id` and are not touched — this
mirrors Stage 22 §6's own precedent that changing what one specialist's
subgraph binds internally requires zero changes to the supervisor, critic,
or the other specialists.

**Why not a closure/factory tool instead** (considered and rejected): an
alternative would build a fresh `search_uploaded_documents` closed over
`user_id` per request (`def make_search_tool(user_id): @tool def
search_uploaded_documents(query): ...`) and re-bind
`knowledge_llm.bind_tools([...])` on every invocation instead of once at
module load. This works, but re-binding an LLM's tools on every request is
both more expensive and less idiomatic than `InjectedState`, which exists
in LangGraph specifically for "give a tool read access to graph state
without giving the model write access to it."

---

## 8. Security Boundaries

What this stage **does** guarantee:

- Given any two distinct `user_id` values, no HTTP response or Knowledge
  Agent answer produced for one will ever contain `document_chunks`
  content, `filename`, or `chunk_id` values belonging to a document owned
  by the other (§6, §11).
- A `document_id` belonging to another user is indistinguishable, from the
  caller's point of view, from a `document_id` that does not exist at all
  (§6) — no existence-oracle leak.
- Every filter value (`user_id`, `document_id`) is passed as a
  parameterized query argument, never string-interpolated into SQL —
  continuing the existing pattern already used throughout Stage 20-22 —
  so a `user_id` value containing SQL metacharacters cannot escape its
  role as a plain equality-filter value.
- The Knowledge Agent's tool cannot be made to search or reveal another
  user's documents through prompt injection or a malicious/compromised
  model response, because `user_id` is never a model-writable tool
  argument (§7).

What this stage explicitly **does not** guarantee (boundary, not a bug):

- That the caller asserting `user_id="alice"` is actually Alice. Nothing
  server-side verifies caller identity (§3, §12) — this is data isolation
  *given* a claimed identity, not authentication of that identity.
- That `thread_id` (the conversation/planner layer) is itself owned by a
  single consistent `user_id` over its lifetime, or that one user cannot
  resume another's paused thread via `/approve`/`/reject`. That is a
  separate, pre-existing gap (thread-level access control), not a
  document-content leak, and is unaddressed by this stage (§12).
- That `POST /documents/backfill-embeddings` is user-scoped. It embeds
  every `document_chunks` row with `embedding IS NULL` regardless of
  owner, exactly as it does today. This is not a retrieval path — its
  response (`BackfillResponse`) contains only counts, never chunk content
  or filenames — so it has nothing to leak and is left unchanged.

---

## 9. Backward Compatibility

- **Existing single-user testing keeps working unchanged**, provided the
  same `user_id` string is supplied consistently across a test run — the
  isolation filter is transparent to a caller that only ever uses one
  identity, the same way today's tests already only ever use one implicit
  "everyone" identity.
- **Pre-Stage-23 documents remain reachable**, not silently orphaned or
  hidden: the migration (§5) assigns them all to the sentinel
  `'default-user'`. A caller who wants to verify old data survived the
  migration queries with `user_id="default-user"`.
- **No endpoint signature is removed or repurposed** — `user_id` is
  strictly additive to `ChatRequest`, `SearchRequest`, and the upload
  form. `/health`, `/approve`, `/reject`, `/documents/backfill-embeddings`
  are byte-for-byte unchanged from Stage 22.
- **`search_knowledge_base` and `knowledge_base/*.md`** (Stage 3, 8, 10,
  16-21) remain completely untouched, same "historical compatibility"
  guarantee Stage 22 §1 already established — this stage doesn't revisit
  that decision.

---

## 10. Error Cases

| Scenario | Code | Detail (hand-written, static) |
|---|---|---|
| `POST /documents/upload` missing `user_id` form field | `422` | (automatic, via FastAPI/Pydantic — same as a missing `file`) |
| `POST /documents/upload` with empty/whitespace-only `user_id` | `400` | `"user_id cannot be empty"` |
| `POST /documents/search` / `POST /chat` missing `user_id` in JSON body | `422` | (automatic, via FastAPI/Pydantic) |
| `POST /documents/search` / `POST /chat` with empty/whitespace-only `user_id` | `400` | `"user_id cannot be empty"` |
| `POST /documents/search` with `document_id` that exists but is owned by a different `user_id` | `404` | `"No document found for this document_id"` (identical to "doesn't exist" — §6, §8) |
| `POST /documents/search` for a `user_id` with zero documents (or zero relevant results) | `200` | `{"results": []}` — not an error, matching Stage 21 §10's existing empty-result behavior |
| Knowledge Agent asked a question when the requesting `user_id` has no uploaded documents | n/a (tool-level) | Tool returns `"No documents have been uploaded yet."`, same graceful string Stage 22 §7 already defined, now naturally scoped per-user by the filter rather than being a system-wide check |

Same philosophy as every prior FastAPI stage in this project: a short,
static, hand-written `detail` string on every `HTTPException`; the real
exception (if any) printed server-side, never echoed to the client.

---

## 11. Testing Requirements

Following this project's standalone-script convention (asserts + prints,
no pytest, `python x_test.py` directly, matching Stage 20-22's test
files) — the key addition over prior stages' tests is that **isolation
must be proven affirmatively**, not just assumed from the filter looking
correct in a single-user test:

- **Two-user upload + cross-search**: upload a document with distinctive
  content as `user_id="alice"`, upload a different document with its own
  distinctive content as `user_id="bob"`. Search as `alice` with a query
  matched to Bob's content → confirm the response never contains Bob's
  `chunk_id`/`document_id`/`content`/`filename`, regardless of how
  semantically close the query is. Repeat symmetrically for Bob searching
  for Alice's content.
- **Knowledge Agent end-to-end cross-user check**: with Alice's and Bob's
  documents both present (as above), ask the Knowledge Agent a question
  as `alice` that is only answerable from Bob's document → confirm the
  tool call result and the specialist's final answer do not contain Bob's
  content, and instead reflect "no relevant/no uploaded documents" from
  Alice's point of view. Assert against the specialist subgraph directly
  (`search_uploaded_documents` invoked with `user_id="alice"`), matching
  Stage 22 §8's precedent for inspecting tool calls the outer graph's
  `messages` state doesn't surface.
- **`document_id` ownership boundary**: as `bob`, call `POST
  /documents/search` with `document_id` set to a document Alice owns →
  confirm `404` with the exact same detail string as an unknown
  `document_id` (not a different message, not a `403`).
- **Same-user round trip still works**: upload as `alice`, search as
  `alice` for content from Alice's own document → confirm it *is*
  returned (proves the filter narrows, rather than always excluding
  everything).
- **Backward-compatible legacy data**: directly insert (or reuse existing)
  `documents`/`document_chunks` rows written before this stage's migration
  ran, confirm the migration leaves them queryable under
  `user_id="default-user"`, and confirm they are *not* returned when
  searching as any other `user_id`.
- **Validation errors**: missing `user_id` on each of the three affected
  endpoints (`422`), empty/whitespace `user_id` on each (`400`) — table in
  §10.
- **Zero-documents-for-this-user is not an error**: a brand-new `user_id`
  that has never uploaded anything gets `200`/`results: []` from
  `/documents/search` and "No documents have been uploaded yet." from the
  Knowledge Agent tool — not a `404` or `500`.
- **SQL-injection-shaped `user_id` value** (e.g. `"alice' OR '1'='1"`) is
  treated as a literal, opaque string — confirm it matches zero documents
  rather than matching everyone's, proving the parameterized-query
  requirement (§8) actually holds in practice, not just in code review.

---

## 12. Explicitly Out of Scope

- **Authentication of any kind** — no passwords, API keys, tokens,
  sessions, or a server-side user registry. `user_id` remains a trusted,
  self-asserted string, exactly like `thread_id` already is (§3, §8).
- **Thread-level / conversation-level ownership.** `POST /approve` and
  `POST /reject` still operate on any `thread_id` presented to them with
  no ownership check; one user resuming another user's paused thread is a
  pre-existing gap this stage does not close, because it's about
  conversation-session access, not document content isolation. Noted as a
  real gap worth its own future stage, not silently ignored.
- **Scoping documents to a `thread_id`/conversation** — Stage 20 §9's
  decision that uploads aren't thread-scoped is unchanged; this stage adds
  a `user_id` owner, not a conversation owner. A given user's documents
  remain visible across all of that user's conversations/threads.
- **User-scoping `POST /documents/backfill-embeddings`** — stays a global
  maintenance operation (§8 explains why this is safe).
- **Any change to `search_knowledge_base` or `knowledge_base/*.md`**
  anywhere they exist (Stage 3, 8, 10, 16-21) — fully preserved.
- **Sharing / multi-owner documents, roles, or permissions beyond a single
  flat owner per document.** One `documents.user_id` per document; no
  concept of a document owned by or shared with more than one user.
- **Rate limiting, quota, or abuse prevention per user** — out of scope,
  unrelated to isolation.
- **Vector index tuning, hybrid/keyword search, reranking** — same
  exclusions as Stage 21 §13 / Stage 22 §9, unchanged here.

---

## 13. Open Decisions to Confirm at Implementation Time

- Exact folder name (`stage23_user_document_isolation` used throughout
  this spec — confirm before scaffolding).
- Whether `UploadResponse` should echo `user_id` back in its JSON body for
  caller-side debuggability (proposed: yes, purely additive, mirrors how
  `filename`/`file_type` are already echoed back).
- Exact sentinel value for the migration default (`'default-user'`
  proposed in §5) — any fixed non-empty string works equally well.
- Whether the "empty `user_id`" validation (§10) belongs before or after
  FastAPI's own field-presence check in the route body, matching how
  Stage 21 §10 already split "missing" (automatic `422`) from "present
  but empty" (hand-written `400`) for `query`.
