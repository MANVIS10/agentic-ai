# Stage 22: Knowledge Agent RAG (Uploaded Documents Only)

## What was added

Stage 21's exact FastAPI app, with one change: the Knowledge Agent's tool
is **replaced**. It now searches user-uploaded documents (`document_chunks`
via `pgvector`, Stage 20/21's tables) instead of the project's bundled
`knowledge_base/*.md` files. `search_knowledge_base` and `knowledge_base/`
are **not** carried into this stage's folder at all — the new tool,
`search_uploaded_documents`, runs Stage 21's cosine-similarity search
in-process and is bound to the Knowledge Agent in its place.

This is a deliberate **replacement, not an addition**. An earlier draft of
this stage considered giving the Knowledge Agent both tools side by side
(bundled docs + uploaded docs); the actual requirement is narrower and
explicit: for normal queries, the Knowledge Agent must answer only from
what the user has uploaded, and the bundled knowledge base must not be
reachable at all. See
[`.claude/spec/stage22_knowledge_agent_rag_spec.md`](../.claude/spec/stage22_knowledge_agent_rag_spec.md)
for the full spec and
[`.claude/plans/stage22_knowledge_agent_rag_plan.md`](../.claude/plans/stage22_knowledge_agent_rag_plan.md)
for the implementation plan.

`search_knowledge_base` and `knowledge_base/*.md` are left **completely
untouched** everywhere they already exist — Stage 3, 8, 10, 16-21 all
still work exactly as before. Nothing about the Research Agent, Analysis
Agent, supervisor, critic, planner, human-approval flow, or the document
upload/backfill/search routes changed.

## New concept

A specialist's tool can be swapped out entirely without touching anything
above it in the graph. `knowledge_node` (inside the supervisor+critic
graph) only ever calls `knowledge_graph.invoke(...)` — it has no idea what
tool is bound inside that subgraph, and never did. Replacing
`search_knowledge_base` with `search_uploaded_documents` only required
editing the Knowledge Agent's own subgraph block; the supervisor's routing
`Literal`, the critic, and the outer planner needed zero changes. This
extends Stage 16's lesson (the critic needs no changes when a specialist
is *added*) one layer deeper: it also holds when a specialist's internals
are *replaced*.

## Architecture

```
Client (curl / TestClient / browser)
      |
      v
FastAPI app (stage22_knowledge_agent_rag/main.py, uvicorn process)
      |
      +-- POST /chat --> plan --> human_approval (interrupt)
      +-- POST /approve --> research_subtask (loop) --> synthesize
      |         |
      |         v
      |     supervisor_critic_graph.invoke(...)
      |         |
      |         +-- "research" --> Research Agent (web search)
      |         +-- "knowledge" --> Knowledge Agent
      |         |         |
      |         |         v
      |         |     search_uploaded_documents(query)
      |         |         |  embed query -> pgvector cosine search over
      |         |         |  document_chunks (all uploaded documents)
      |         |         v
      |         |     [source: filename]\n{chunk text}, or
      |         |     "No documents have been uploaded yet."
      |         +-- "analysis" --> Analysis Agent (calculate)
      |         |
      |         v
      |     critic_node --> pass / retry (same specialist, bounded)
      |
      +-- POST /documents/upload, /documents/backfill-embeddings,
      |   /documents/search --> unchanged from Stage 21
      v
PostgreSQL + pgvector (docker-compose `postgres` service,
      image pgvector/pgvector:pg16, host port 5433)
```

## The Knowledge Agent's new tool

```python
@tool
def search_uploaded_documents(query: str) -> str:
    """Search documents the user has uploaded ..."""
```

- Embeds `query` with the same `OpenAIEmbeddings(model="text-embedding-3-small")`
  instance used elsewhere in this file.
- Runs the same cosine-similarity query pattern as `POST /documents/search`,
  directly against the shared `pg_conn` — not an HTTP call to that route.
- Fixed `k=3` (matching `search_knowledge_base`'s original `k=3`), not a
  caller-configurable `top_k` — this is an internal agent tool, not a
  testing endpoint.
- No `document_id` scoping: uploads aren't tied to a thread or user (an
  unchanged Stage 20 decision), so there's nothing to scope by yet. The
  tool searches every uploaded document.
- No similarity threshold. `ORDER BY ... LIMIT k` always returns the
  closest `k` chunks whenever *any* embedded chunk exists — there's no
  SQL-detectable "matched zero relevant chunks" state without one. The
  only reachable empty case is "zero uploaded chunks have an embedding at
  all" → `"No documents have been uploaded yet."`. Otherwise the tool
  returns its top-3 chunks and lets the specialist LLM judge relevance
  itself and say so in its answer — exactly how `search_knowledge_base`
  has always behaved.
- Fails gracefully: an embedding-API error or DB error is caught and
  returned as a plain string, never raised into the graph (matching the
  Stage 4/5/15 "tool fails gracefully" principle — a `@tool` function
  can't raise `HTTPException` the way a route can).

## Endpoints

Identical to Stage 21 — no route signatures, request/response shapes, or
status codes changed:

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Unchanged |
| POST | `/chat` | Unchanged. A knowledge-shaped question now gets answered from uploaded documents instead of the bundled knowledge base |
| POST | `/approve` | Unchanged |
| POST | `/reject` | Unchanged |
| POST | `/documents/upload` | Unchanged |
| POST | `/documents/backfill-embeddings` | Unchanged |
| POST | `/documents/search` | Unchanged — kept for direct testing/inspection of the search layer, independent of the agent's own tool |

## Design decisions

- **Replacement, not addition.** The default, more conservative option
  (bind both `search_knowledge_base` and a new uploaded-documents tool
  side by side) was explicitly overridden per the requirement that bundled
  content must not be used for normal queries — this isn't a "future
  work" deferral, it's a deliberate exclusion.
- **`knowledge_base/` not duplicated into this stage's folder.** Nothing
  in this stage's code reads it, so copying it forward (as every other
  stage folder does with the resources it needs) would be dead weight.
  Historical compatibility lives in Stage 3-21's own folders, not here.
- **No similarity threshold in the tool.** See "The Knowledge Agent's new
  tool" above — keeps behavior simple and matches
  `search_knowledge_base`'s own threshold-free design; the LLM judges
  relevance from returned content either way.
- **`search_uploaded_documents` queries Postgres in-process, not via HTTP
  to `/documents/search` on itself.** Same process, same `pg_conn`,
  same `embeddings` instance already at module scope — an HTTP round trip
  to its own server would add latency and failure modes for no benefit.

## How to run

Same as Stage 21 — requires `docker-compose.yml`'s `pgvector/pgvector:pg16`
image and no new dependencies.

```
docker compose up -d
.venv\Scripts\activate
pip install -r requirements.txt
python stage22_knowledge_agent_rag/main.py
```

## Running the tests

```
python stage22_knowledge_agent_rag/test_knowledge_agent_rag.py
```

Covers: the Knowledge Agent reporting honestly when no documents have been
uploaded (checked defensively — this project's shared dev database usually
already has chunks from other stages/prior runs, so this check is skipped
rather than asserted false when that's the case); uploading a distinctive
document and confirming both that `search_uploaded_documents` was actually
called (inspected directly on `knowledge_graph`'s messages, since the
outer graph doesn't surface it — Stage 16) and that the final answer
reflects the uploaded content; that a fact unique to bundled
`knowledge_base/wind.md` (untouched, not duplicated here) never leaks into
an answer, proving isolation actually holds; and that supervisor routing
to the Knowledge Agent is unaffected.

## What changed compared with Stage 21

| | Stage 21 | Stage 22 |
|---|---|---|
| Knowledge Agent's tool | `search_knowledge_base` (bundled `knowledge_base/*.md`, `InMemoryVectorStore`) | `search_uploaded_documents` (`document_chunks` via `pgvector`) |
| Knowledge Agent's system prompt | "local knowledge base of documents" | "documents the user has uploaded" |
| `knowledge_base/` folder | Present, loaded at startup | Not present in this stage's folder |
| Research Agent, Analysis Agent | — | Byte-identical to Stage 21 |
| Supervisor, critic, planner, routes | — | Byte-identical to Stage 21 |
| New dependencies / infrastructure | — | None |

Stage 21 proved uploaded chunks could be searched. Stage 22 proves a
specialist's *entire retrieval source* can be swapped without touching the
routing, review, or planning layers built on top of it — the same
composability lesson this project has demonstrated at every layer since
Stage 8, now applied to what a specialist searches rather than which
specialist gets picked.
