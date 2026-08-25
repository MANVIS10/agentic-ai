# Stage 23: Per-User Document Isolation

## What was added

Stage 22's exact FastAPI app, with one change threaded through three
places: every uploaded document now has an owner (`user_id`), and both
retrieval paths — `POST /documents/search` and the Knowledge Agent's own
`search_uploaded_documents` tool — filter by it. Stage 20-22 stored and
searched every uploaded document in one shared, unowned pool; this stage
closes that gap so **User A can never retrieve User B's documents.**

`user_id` is a required, caller-supplied string on `POST /documents/upload`,
`POST /documents/search`, and `POST /chat` — trusted at face value, the
same way `thread_id` always has been in this project. This is **not**
authentication; nothing verifies the caller "is" the `user_id` it claims.
See
[`.claude/spec/stage23_user_document_isolation_spec.md`](../.claude/spec/stage23_user_document_isolation_spec.md)
for the full spec, including why real auth is explicitly out of scope.

## New concept

**A tool bound to an LLM can read trusted, server-side context the model
itself can never see or set.** `search_uploaded_documents`'s `query`
argument is still fully model-controlled (unchanged from Stage 22), but its
new `user_id` argument is populated by `ToolNode` directly from the graph
state via `langgraph.prebuilt.InjectedState` — `bind_tools` excludes it
from the schema the LLM sees entirely. The obvious alternative (a plain
`user_id: str` tool argument) would let a compromised or merely confused
model call the tool with someone else's `user_id`; `InjectedState` closes
that off structurally rather than by prompting the model not to.

`user_id` otherwise flows exactly like `question`/`thread_id` already do —
one more field threaded through graphs that already pass state between each
other by plain function call (Stage 17/22's lesson): `PlannerState` →
`CriticState` → a new `KnowledgeState` (the Knowledge Agent subgraph's own
`MessagesState` subclass).

## Architecture

```
Client (curl / TestClient / browser)
      |
      v
FastAPI app (stage23_user_document_isolation/main.py, uvicorn process)
      |
      +-- POST /chat {question, thread_id, user_id}
      |         |
      |         v
      |     graph.invoke({question, user_id}, config)   <- PlannerState.user_id
      |         |   (checkpointed - survives the human_approval interrupt/resume,
      |         |    so /approve/reject need no user_id of their own)
      |         v
      |     research_subtask()
      |         |  supervisor_critic_graph.invoke({messages, user_id})
      |         v                                        <- CriticState.user_id
      |     knowledge_node()
      |         |  knowledge_graph.invoke({messages, user_id})
      |         v                                        <- KnowledgeState.user_id
      |     knowledge_agent_node() -> ToolNode
      |         |  search_uploaded_documents(query=..., user_id=<INJECTED, not model-set>)
      |         v
      |     WHERE d.user_id = %s   (filtered in Postgres)
      |
      +-- POST /documents/upload {file, user_id}
      |         -> documents.user_id set on insert
      |
      +-- POST /documents/search {query, user_id, ...}
      |         -> WHERE d.user_id = %s, unconditional on every search;
      |            a document_id owned by someone else 404s identically
      |            to a document_id that doesn't exist
      |
      +-- POST /approve, /reject, /documents/backfill-embeddings, /health
      |         -> byte-identical to Stage 22
      v
PostgreSQL + pgvector (docker-compose `postgres` service,
      image pgvector/pgvector:pg16, host port 5433)
      documents.user_id  TEXT NOT NULL DEFAULT 'default-user'  (new)
      idx_documents_user_id  (new, plain B-tree index)
```

## Data model change

One new column, migrated the same idiom Stage 21 used to evolve
`document_chunks` (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, run
unconditionally at module load):

```sql
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default-user';

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents (user_id);
```

`NOT NULL DEFAULT 'default-user'`, not a nullable column: this project runs
every stage against one shared Postgres database (Stage 18 onward), so
`documents` already had rows from Stage 20-22 testing with no owner.
Running this `ALTER TABLE` backfills every one of them to the literal
string `'default-user'` in the same statement that adds the column — there
is never a row with an unowned/`NULL` `user_id`, so no filtering query
anywhere needs to special-case one. Confirmed directly: importing this
stage's `main.py` against the existing shared database backfilled all 9
pre-existing `documents` rows to `'default-user'`, verified with
`SELECT user_id, count(*) FROM documents GROUP BY user_id`.

`document_chunks` gets no new column — `user_id` lives on `documents`
only, and every query that needs it already joins `document_chunks` to
`documents` for `filename` (Stage 21/22's search queries), so the filter
is one more `AND` on an existing join rather than a new one.

## Search filtering

Both retrieval paths gained the identical rule: **no query may return a
`document_chunks` row whose parent `documents.user_id` doesn't match the
requesting `user_id`.**

`search_uploaded_documents` (the Knowledge Agent's tool):

```python
@tool
def search_uploaded_documents(
    query: str,
    user_id: Annotated[str, InjectedState("user_id")],
) -> str:
    ...
    # WHERE dc.embedding IS NOT NULL AND d.user_id = %s
```

`POST /documents/search`: the same `AND d.user_id = %s` added to the
existing cosine-similarity query, and the optional `document_id` ownership
check now also requires `AND user_id = %s` — a `document_id` that exists
but belongs to someone else returns the *exact same* `404` as one that
doesn't exist at all, never a different status code or message (no
existence-oracle leak).

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Unchanged |
| POST | `/chat` | **New required field: `user_id`.** Validated (`400` if empty), threaded into the graph as `PlannerState.user_id` |
| POST | `/approve` | Unchanged — resumes from checkpointed state, no `user_id` field needed |
| POST | `/reject` | Unchanged |
| POST | `/documents/upload` | **New required form field: `user_id`.** Stored as the document's owner; echoed back in the response |
| POST | `/documents/backfill-embeddings` | Unchanged — stays global on purpose (§8 of the spec: its response is only counts, nothing to leak) |
| POST | `/documents/search` | **New required field: `user_id`.** Every result and the `document_id` ownership check are scoped to it |

## Design decisions

- **Not authentication.** `user_id` is a trusted, caller-supplied string —
  the same trust model this project has always used for `thread_id`. What
  this stage guarantees is narrower and still meaningful: *given* a
  `user_id`, the system never returns another `user_id`'s document
  content. Whether a caller is honest about which `user_id` it is remains
  outside this stage's boundary.
- **Plain request fields, not a custom header.** A `X-User-Id` header
  would centralize the field into one dependency, but no route in this
  project has ever read application data from a header — three small,
  explicit field additions stay consistent with how Stage 19-22 already
  read.
- **`InjectedState`, not a per-request tool closure.** An alternative
  design would build a fresh `search_uploaded_documents` closed over
  `user_id` on every request and re-bind the LLM's tools each time.
  `InjectedState` is what LangGraph provides specifically for "give a tool
  read access to graph state without giving the model write access to
  it" — cheaper and more idiomatic than rebuilding the tool binding per
  call.
- **`documents.user_id`, not a `document_chunks.user_id`.** Every query
  that needs ownership already joins to `documents` for `filename`, so
  denormalizing the owner onto `document_chunks` too would just be a
  second copy of the same fact that could drift — not worth it at this
  project's scale (same call Stage 21 made about not adding a redundant
  `chunk_embeddings` table).
- **`POST /documents/backfill-embeddings` stays global, not user-scoped.**
  It's a maintenance operation whose response contains only counts, never
  chunk content or filenames — there's nothing here for one `user_id` to
  leak to another.
- **Thread-level ownership is a known, separate gap, not fixed here.**
  `/approve`/`/reject` still act on any `thread_id` with no ownership
  check — that's conversation-session access control, a different problem
  from document content isolation, and explicitly out of scope for this
  stage (see the spec §12).

## How to run

Same as Stage 21/22 — requires `docker-compose.yml`'s `pgvector/pgvector:pg16`
image and no new dependencies.

```
docker compose up -d
.venv\Scripts\activate
pip install -r requirements.txt
python stage23_user_document_isolation/main.py
```

## Running the tests

```
python stage23_user_document_isolation/test_user_document_isolation.py
```

Two users (`alice`/`bob`, unique-suffixed per run) each upload a document
with distinctive, made-up content, then every retrieval path is checked
from both directions — proving isolation affirmatively rather than
assuming it from a single-user test:

- Cross-user `POST /documents/search`: alice searching for bob's content
  (and vice versa) never returns the other's document, while each can
  still find their own.
- Full Knowledge Agent subgraph, end to end: asking as alice about a fact
  that only exists in bob's document produces an honest "not found," not a
  fabricated or leaked answer — checked against the actual confidential
  figure in bob's document, not just a term echoed back from the question.
- `search_uploaded_documents.invoke(...)` called directly with different
  injected `user_id` states, independent of any particular LLM's tool-call
  behavior in the checks above.
- `document_id` ownership: a document_id belonging to another user 404s
  with the identical status code and detail string as an unknown one.
- A `user_id` shaped like a SQL injection attempt (`"alice' OR '1'='1"`)
  matches zero documents, proving parameterization holds in practice.
- Missing/empty `user_id` on each of the three affected endpoints (`422`
  automatic / `400` hand-written, matching this project's existing
  "missing vs. present-but-empty" split).
- A brand-new `user_id` with zero documents gets an empty result, not an
  error, from both retrieval paths.
- Pre-Stage-23 rows (this project's shared database already had 9 from
  Stage 20-22 testing) remain reachable under the `'default-user'`
  migration sentinel.

Confirmed as a regression check too: Stage 22's own test file
(`stage22_knowledge_agent_rag/test_knowledge_agent_rag.py`) still passes
unmodified against the now-migrated shared database — the new column and
index are additive and don't affect any query that doesn't reference them.

## What changed compared with Stage 22

| | Stage 22 | Stage 23 |
|---|---|---|
| `documents` table | No owner concept | `user_id TEXT NOT NULL DEFAULT 'default-user'` + index |
| `search_uploaded_documents` | Searches every uploaded document | Scoped to the calling `user_id` via `InjectedState` |
| `POST /documents/search` | Searches every uploaded document | Scoped to `request.user_id`; `document_id` ownership enforced |
| `POST /chat` | `{question, thread_id}` | `{question, thread_id, user_id}` |
| `POST /documents/upload` | `{file}` | `{file, user_id}` |
| `PlannerState` / `CriticState` | No `user_id` field | `user_id: str` added to both |
| Knowledge Agent subgraph state | Plain `MessagesState` | New `KnowledgeState(MessagesState)` with `user_id` |
| Research Agent, Analysis Agent | — | Byte-identical to Stage 22 |
| Supervisor, critic, planner shape, `/approve`, `/reject`, `/documents/backfill-embeddings`, `/health` | — | Byte-identical to Stage 22 |
| New dependencies | — | None (`InjectedState` is already part of the installed `langgraph.prebuilt`) |

Stage 22 proved a specialist's entire retrieval source can be swapped
without touching the routing/review/planning layers above it. Stage 23
proves the same layers can carry one more piece of trusted context all the
way down to a tool call — invisibly to the model — without any of those
layers needing to know or care what that context is used for.
