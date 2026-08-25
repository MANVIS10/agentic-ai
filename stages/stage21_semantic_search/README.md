# Stage 21: Embeddings & Semantic Vector Search

## What was added

Stage 20's exact FastAPI app, plus embeddings and semantic search over
uploaded document chunks. `document_chunks` (Stage 20's table) gains one
new column, `embedding vector(1536)`, populated automatically for every
new upload and backfillable for chunks that predate this stage. Two new
endpoints: `POST /documents/backfill-embeddings` and
`POST /documents/search`.

Nothing about the existing graph, specialists, supervisor, critic,
planner, human-approval flow, or `/documents/upload`'s validation/
extraction/chunking logic changed. Every node, edge, tool, prompt, and
route other than the additions below is copied verbatim from
[`stage20_document_upload/main.py`](../stage20_document_upload/main.py).

This stage is deliberately **search only**. See
[`.claude/spec/stage21_semantic_search_spec.md`](../.claude/spec/stage21_semantic_search_spec.md)
for the full spec. No LLM reads search results, no agent gains a new
tool, and no existing routing changes — that's Stage 22.

## New concept

Every earlier embedding/search capability in this project
(`search_knowledge_base`, Stage 3 onward) rebuilds an in-process
`InMemoryVectorStore` from scratch every time the process starts. This
stage stores embeddings durably in PostgreSQL instead, via the
[`pgvector`](https://github.com/pgvector/pgvector) extension — the
vectors themselves survive a process restart, the same way Stage 18 made
checkpoints and Stage 20 made `document_chunks` durable.

`document_chunks` gaining a column is also the **first schema evolution**
in this repo — every earlier DDL (including Stage 20's own) only ever
used `CREATE TABLE IF NOT EXISTS` for a brand-new table.
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is a different, and new, kind
of idempotent statement: it changes a table that already has rows in it.
Because Stage 20 and Stage 21 both write to the *same* Postgres database,
any `document_chunks` rows written by Stage 20 (or an unmodified Stage 20
process still running) have `embedding IS NULL` — `POST
/documents/backfill-embeddings` exists specifically to embed those rows
after the fact.

## Architecture

```
Client (curl / TestClient / browser)
      |
      v
FastAPI app (stage21_semantic_search/main.py, uvicorn process)
      |
      +-- POST /documents/upload --> extract -> chunk -> embed (batched,
      |                               embed_documents) -> INSERT (1 txn)
      |
      +-- POST /documents/backfill-embeddings --> SELECT ... WHERE
      |                               embedding IS NULL -> embed each ->
      |                               UPDATE (per chunk, independently)
      |
      +-- POST /documents/search --> embed_query -> pgvector cosine
      |                               distance (<=>) -> ranked results
      v
PostgreSQL + pgvector extension (docker-compose `postgres` service,
      image pgvector/pgvector:pg16, host port 5433)
```

### Upload pipeline (updated from Stage 20)

```
Upload -> Validate -> Extract text -> Chunk -> Embed (batched) -> Store
                                                  ^^^^^^^^^^^^^^ new
```

Embedding happens *before* the database transaction opens: if
`embeddings.embed_documents(chunks)` fails, nothing is written — the
whole upload fails with a `500`, exactly like a DB write failure, rather
than storing some chunks with an embedding and others without.

### Backfill

```
SELECT id, content FROM document_chunks WHERE embedding IS NULL
      |
      v
for each chunk: embed_documents([content])[0] -> UPDATE ... SET embedding
      (independently, one try/except per chunk)
```

Each chunk is embedded and updated on its own, not as one giant batch —
one bad chunk never blocks the rest, and re-running this endpoint only
ever touches rows still `NULL`. It's naturally resumable with no extra
state to track.

## Database schema

```sql
-- Stage 20, unchanged:
CREATE TABLE IF NOT EXISTS documents (...)
CREATE TABLE IF NOT EXISTS document_chunks (...)

-- Stage 21, new:
CREATE EXTENSION IF NOT EXISTS vector
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(1536)
```

`embedding` is nullable on purpose — a chunk without one is simply
excluded from search results (`WHERE dc.embedding IS NOT NULL`), not an
integrity violation. No new table for embeddings: one row already exists
per chunk in `document_chunks`, so a nullable column there keeps the
one-chunk/one-embedding relationship trivial (no join needed for search).
No vector index (`ivfflat`/`hnsw`) is created — a sequential scan is fine
at this project's scale; indexing is a future optimization, not a
correctness requirement here.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | Unchanged from Stage 20 |
| POST | `/chat` | `{"question": str, "thread_id": str}` | Unchanged from Stage 20 |
| POST | `/approve` | `{"thread_id": str}` | Unchanged from Stage 20 |
| POST | `/reject` | `{"thread_id": str}` | Unchanged from Stage 20 |
| POST | `/documents/upload` | `multipart/form-data`, field `file` | Same response shape as Stage 20; chunks are now embedded automatically |
| POST | `/documents/backfill-embeddings` | — | `{"chunks_found": int, "embedded_count": int, "failed_count": int}` |
| POST | `/documents/search` | `{"query": str, "top_k"?: int, "similarity_threshold"?: float, "document_id"?: str}` | `{"query": str, "results": [{"chunk_id", "document_id", "filename", "chunk_index", "content", "similarity"}]}` |

`top_k` defaults to `5`. `similarity_threshold` and `document_id` are
optional — omit either to search unfiltered/unrestricted. An empty
`results` list is a valid `200`, not an error.

### Status codes for `/documents/search`

| Scenario | Code | Detail |
|---|---|---|
| Missing/malformed request body | `422` | automatic, via FastAPI/Pydantic |
| Empty or whitespace-only `query` | `400` | `"Query text cannot be empty"` |
| `top_k` not a positive integer | `400` | `"top_k must be a positive integer"` |
| `similarity_threshold` outside `[0, 1]` | `400` | `"similarity_threshold must be between 0 and 1"` |
| `document_id` given but malformed or does not exist | `404` | `"No document found for this document_id"` |
| Embedding the query fails (OpenAI API error) | `500` | `"Something went wrong while processing this search. Please try again."` |
| Unexpected DB error | `500` | `"Something went wrong while processing this search. Please try again."` |

Same philosophy as every other route: a short, static, hand-written
`detail` string on every `HTTPException`, the real exception printed
server-side, and the existing global `@app.exception_handler(Exception)`
still applies as defense-in-depth.

## Example requests

```bash
curl -X POST http://127.0.0.1:8000/documents/backfill-embeddings

curl -X POST http://127.0.0.1:8000/documents/search \
  -H "Content-Type: application/json" \
  -d '{"query": "How does a wind turbine generate electricity?", "top_k": 5, "similarity_threshold": 0.75}'
```

PowerShell:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/documents/backfill-embeddings

Invoke-RestMethod -Method Post http://127.0.0.1:8000/documents/search `
  -ContentType "application/json" `
  -Body '{"query": "How does a wind turbine generate electricity?", "top_k": 5}'
```

## Design decisions

- **No vector index yet.** A sequential scan is fine at this project's
  scale (a handful of uploaded documents, dozens to low hundreds of
  chunks). `ivfflat`/`hnsw` indexing is a documented future optimization,
  not built here — see the spec's "explicitly out of scope."
- **Nullable `embedding` column on the existing table, not a second
  `chunk_embeddings` table.** One row already exists per chunk; adding a
  column keeps the relationship trivial (no join needed for search) and
  avoids a redundant table with its own FK back to `document_chunks`.
- **`document_id` kept as `str`, not `uuid.UUID`, in `SearchRequest`.** If
  Pydantic parsed it as a `UUID`, a malformed value would auto-`422`
  before the route body ever runs — but the spec only defines a `404` for
  "does not exist." Parsing manually with `uuid.UUID(...)` inside a
  `try/except ValueError`, mapped to that same `404`, makes "malformed and
  unknown both → 404" actually true.
- **Two separate `try/except` blocks in `upload_document()`** (embedding,
  then the DB transaction) so server logs distinguish an embedding-API
  failure from a DB failure, even though both return the identical
  client-facing `500` — the spec doesn't require different client-visible
  messages, just accurate server-side diagnostics.
- **Backfill embeds one chunk at a time, not one batched call for every
  `NULL` row.** A single `embed_documents()` call over many rows fails
  all-or-nothing; embedding (and updating) each chunk independently means
  one bad chunk never blocks the rest, and a retry is automatically
  scoped to whatever's still `NULL` — no extra bookkeeping needed.
- **Every embedding value is wrapped in `pgvector.Vector(...)` before
  touching `pg_conn.execute(...)`.** `pgvector`'s psycopg adapter
  registers dumpers for `Vector` and `numpy.ndarray` only, never a plain
  `list` — and `OpenAIEmbeddings.embed_documents()`/`.embed_query()`
  return plain `list[float]`. Skipping the wrapper would let psycopg
  silently adapt the value as a generic array instead of a `vector`,
  breaking every INSERT/UPDATE/search query that touches it.
- **Cosine distance (`<=>`), not L2 (`<->`) or inner product (`<#>`).**
  Matches the metric embedding-based similarity search conventionally
  uses, and what `InMemoryVectorStore.similarity_search` already does
  under the hood for the bundled knowledge base.

## How to run

This stage requires `docker-compose.yml`'s Postgres image to support
`pgvector` — the plain `postgres:16` image doesn't ship its extension.
The image was swapped to `pgvector/pgvector:pg16` (same Postgres 16 base,
same `langgraph_postgres_data` volume, no data migration needed) as part
of this stage's setup.

Start Postgres (from the repo root, if not already running):

```
docker compose up -d
```

Install dependencies (adds `pgvector` — the Python package — to the
existing set):

```
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the API:

```
python stage21_semantic_search/main.py
```

## Running the tests

```
python stage21_semantic_search/test_semantic_search.py
```

Covers, all through `fastapi.testclient.TestClient` (no running server
needed, but the real OpenAI embeddings API is called — no mocking, same
as every prior stage's test file): an upload populating every resulting
chunk's embedding at 1536 dimensions; a manually-inserted `NULL`-embedding
row (simulating a pre-Stage-21 chunk) getting populated by
`/documents/backfill-embeddings`; a topically-relevant query ranking the
matching document above an unrelated one; `top_k` being respected;
`similarity_threshold` excluding low-similarity results; `document_id`
scoping never leaking another document's chunks into results; and the
`400`/`404`/`422` error cases (empty query, invalid `top_k`, invalid
threshold, unknown `document_id`, malformed `document_id`, missing
`query` field).

## What changed compared with Stage 20

| | Stage 20 | Stage 21 |
|---|---|---|
| `document_chunks` schema | `id, document_id, chunk_index, content, created_at` | + `embedding vector(1536)` |
| `/documents/upload` | validate → extract → chunk → store | validate → extract → chunk → embed → store |
| New routes | — | `POST /documents/backfill-embeddings`, `POST /documents/search` |
| New infrastructure | — | `docker-compose.yml` image swapped to `pgvector/pgvector:pg16` |
| New dependencies | — | `pgvector` (Python package) |
| Graph logic (nodes, edges, prompts, tools) | — | Byte-identical to Stage 20 |

Stage 20 proved arbitrary user file uploads could be durably stored.
Stage 21 proves that storage can grow a completely new capability
(semantic search) via a single additive column and two new routes,
without touching the ingestion pipeline's validation/extraction/chunking
logic or any of the existing graph/routes at all.
