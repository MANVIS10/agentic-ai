# Stage 21 Specification — Embeddings & Semantic Vector Search

## 0. Status

```text
Stage 19  FastAPI HTTP backend        ✅ (deliberate extension, post-roadmap)
Stage 20  Document upload/ingest      ✅ (deliberate extension, post-roadmap)
Stage 21  Embeddings + vector search  → Next (this spec)
Stage 22  RAG / Knowledge Agent integration  ⏳ (future, out of scope here)
```

Like Stage 18-20, Stage 21 is a deliberate extension past the original
roadmap in `spec_document.md`, not a numbered item from it. It builds on
Stage 20's document-ingestion pipeline (`stage20_document_upload/`) the
same way every prior stage built on the one before it: a new stage folder
that duplicates what it needs rather than editing the previous stage in
place, per `CLAUDE.md`'s "previous stages left untouched, no shared
`common/` module" rule.

This document is a **specification only**. No implementation code is
written against it yet.

---

## 1. Purpose

Stage 20 made `document_chunks` durable but inert — every chunk is stored
as plain text with no way to find "which chunks are actually relevant to
this question" other than reading every row. Stage 3 (and every stage
since that reuses `search_knowledge_base`) already proved the answer to
that problem for the project's *bundled* markdown docs: embed each chunk,
compare embeddings by similarity, return the closest matches. Stage 21
applies that same idea to Stage 20's *user-uploaded* chunks, backed by
real durable vector storage instead of Stage 3's rebuilt-every-process
`InMemoryVectorStore`.

This is again deliberately narrow in scope: it makes uploaded chunks
**searchable**, but does not make them **answerable** yet. No LLM reads
the search results, no agent gains a new tool, and no existing routing
changes. The reason to isolate this from RAG/Knowledge Agent integration
(Stage 22, previewed in §10) is the same reason Stage 20 isolated
ingestion from search in the first place: one new concept per stage,
testable and readable on its own before the next stage composes it into
something bigger.

**How this eventually helps the Knowledge Agent:** once Stage 22 wires a
retrieval tool on top of the semantic search this stage builds, the
Knowledge Agent (currently scoped to the bundled `knowledge_base/*.md`
files only, via `search_knowledge_base`) will be able to answer from
user-uploaded documents too — without Stage 21's embedding/search code
needing to change at all, the same "pluggable backend behind a stable
interface" separation Stage 18 proved for checkpointers.

---

## 2. Scope of Step 21

In scope:

- Generating an embedding for every chunk stored by the upload pipeline
  (new uploads going forward).
- Backfilling embeddings for chunks that already exist in
  `document_chunks` without one (rows written by Stage 20 before this
  stage existed, since both stages share the same Postgres database).
- Durable vector storage in PostgreSQL via the `pgvector` extension —
  not a second in-memory or on-disk vector store.
- Cosine-similarity search over stored embeddings, with configurable
  `top_k` and an optional similarity threshold.
- A dedicated HTTP endpoint for exercising semantic search directly
  (for testing/inspection — not for answering questions).

Explicitly not in scope — see §9.

---

## 3. Embedding Model

Reuses `OpenAIEmbeddings(model="text-embedding-3-small")` — the exact
model already used by every Knowledge Agent's `load_knowledge_base()`
(Stage 3, 8, 10, 16-20), rather than introducing a second embedding
model/provider into the project. This model produces **1536-dimensional**
vectors, which fixes the `vector(1536)` column width in §5.

No new embedding-related dependency beyond what's already installed
(`langchain-openai`); embedding calls go through the same `OPENAI_API_KEY`
already read from `.env`.

---

## 4. Embedding Generation for Document Chunks

Two paths populate `document_chunks.embedding` (see §5):

1. **At upload time** — Stage 21's own copy of the `/documents/upload`
   pipeline (duplicated from Stage 20, per this project's convention)
   embeds each chunk right after chunking, before the insert transaction,
   so every chunk written by Stage 21's upload route is born with an
   embedding already attached. No separate "embed after storing" step or
   background job — one embedding call per chunk, synchronously, same
   request.
2. **Backfill for pre-existing chunks** — because Stage 20 and Stage 21
   both write to the *same* Postgres database (this project has one
   shared instance, not per-stage schemas — see `docker-compose.yml`),
   any `document_chunks` rows written by Stage 20 (or by Stage 21 before
   this feature existed) have `embedding IS NULL`. A backfill mechanism
   (endpoint or standalone script — exact shape decided at implementation
   time) finds rows with `embedding IS NULL`, embeds their `content`, and
   updates them in place. This is the only way search (§6) can ever see
   chunks that predate this stage.

Embedding calls are batched where practical (`OpenAIEmbeddings.embed_documents(...)`
over a list of chunk texts) rather than one call per chunk in a loop, to
avoid unnecessary round trips — the same instinct already applied
implicitly by `vector_store.add_documents(...)` in every existing
Knowledge Agent loader.

---

## 5. Vector Storage in PostgreSQL

`document_chunks` (Stage 20's table) gains one new column:

```sql
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(1536);
```

`ADD COLUMN IF NOT EXISTS`, not `CREATE TABLE IF NOT EXISTS`, because the
table already exists (created by Stage 20) — this is the first schema
*evolution* in this repo, as opposed to first-time table creation.
Idempotent and safe to run at every process start, matching the existing
`checkpointer.setup()` / Stage 20 DDL convention.

No new table for embeddings — one row per chunk already exists in
`document_chunks`; adding a nullable column there keeps the one-chunk/
one-embedding relationship trivial (no join needed for search) and
avoids introducing a redundant `chunk_embeddings` table with its own FK
back to `document_chunks`.

`embedding` is nullable, precisely so pre-Stage-21 rows and rows still
awaiting backfill remain valid rows — a chunk without an embedding is
just not returned by search (§6), not an integrity violation.

No vector index (e.g. `ivfflat`/`hnsw`) is created in this stage — a
sequential scan is fine at this project's scale (a handful of uploaded
documents, dozens to low hundreds of chunks). Noted as a future
optimization, not a correctness requirement here.

---

## 6. pgvector Requirements & Setup

**This requires a Postgres image change.** The current
`docker-compose.yml` uses the plain `postgres:16` image, which does not
ship the `pgvector` extension's shared library — `CREATE EXTENSION
vector` fails against it. This stage needs the compose file's image
switched to `pgvector/pgvector:pg16` (the official pgvector image, built
on top of the same `postgres:16` base, so the existing
`langgraph_postgres_data` volume remains compatible — no data migration,
since it's the same Postgres major version with one additional
extension's library added).

This is a **shared-infrastructure change** (like `docker-compose.yml`
itself was when Stage 18 introduced it), not a per-stage file — it
affects the one Postgres instance every stage from 18 onward connects to.
It needs explicit call-out and approval before implementation, since
(unlike a new stage folder) it touches something stages 18-20 already
depend on. It is expected to be backward compatible (every existing
capability of the plain `postgres:16` image is retained; `pgvector` is
additive), but that compatibility should be verified in practice before
this is treated as a formality.

Once the image supports it, enabling the extension is idempotent
application-level SQL, run at module load like every other DDL in this
project:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

**New Python dependency**: the `pgvector` package (`pip install pgvector`,
PyPI name `pgvector`), which registers a `vector` type adapter for
`psycopg` so a Python `list[float]` can be inserted/read directly as a
Postgres `vector` value (`from pgvector.psycopg import register_vector`)
instead of hand-formatting `'[0.1,0.2,...]'::vector` string literals.
Chosen for the same reason Stage 20 accepted `python-docx`/
`python-multipart` as new dependencies: it's the standard, minimal way to
do this, not a heavyweight abstraction over the SQL itself (this project
still writes its own `SELECT ... ORDER BY embedding <=> %s` queries by
hand — see §7 — rather than adopting a full vector-store library like
`langchain_postgres.PGVector`, consistent with Stage 20's own "raw SQL,
not an ORM" choice).

---

## 7. Semantic Similarity Search

Cosine similarity via pgvector's `<=>` operator (cosine *distance*;
similarity = `1 - distance`), matching the metric embedding-based
similarity search conventionally uses, and consistent with what
`InMemoryVectorStore.similarity_search` already does under the hood for
the bundled knowledge base:

```sql
SELECT dc.id, dc.document_id, dc.chunk_index, dc.content,
       1 - (dc.embedding <=> %(query_embedding)s) AS similarity,
       d.filename
FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
WHERE dc.embedding IS NOT NULL
ORDER BY dc.embedding <=> %(query_embedding)s
LIMIT %(top_k)s
```

A similarity threshold (§8) is applied as a `HAVING`/`WHERE` filter on
the computed similarity, or filtered in Python after the query — exact
placement decided at implementation time, but the *result* must be
identical either way: chunks below the threshold never appear in the
response, regardless of `top_k`.

The query text itself is embedded with the same model (§3) as the stored
chunks — comparing embeddings from two different models would be
meaningless.

---

## 8. Configurable `top_k` and Similarity Threshold

Both are request-level parameters on the search endpoint (§9), not
hardcoded constants — unlike `search_knowledge_base`'s fixed `k=3`, this
endpoint exists specifically to let a caller experiment with search
behavior while testing.

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `query` | string | yes | — | Text to embed and search for |
| `top_k` | integer | no | `5` | Maximum number of results; must be a positive integer |
| `similarity_threshold` | float | no | none (no filtering) | If given, must be in `[0, 1]`; results below this similarity are excluded |
| `document_id` | string (UUID) | no | none (search all documents) | If given, restrict the search to chunks from one document |

---

## 9. Document/Chunk Relationships

Unchanged from Stage 20: one `documents` row → many `document_chunks`
rows via `document_chunks.document_id` (`ON DELETE CASCADE`),
`chunk_index` preserving original order. Stage 21 adds one column
(`embedding`) to the existing `document_chunks` table (§5) — no new
tables, no new relationships. Search results are joined back to
`documents` (§7's query) so a result can report which uploaded file
(`filename`) a chunk came from, the same way `search_knowledge_base`
tags every result with its `source`.

---

## 10. API Design for Testing Semantic Search

### `POST /documents/search`

Added to Stage 21's own copy of the FastAPI app (duplicated from Stage
20, same as every prior stage's "duplicate + extend" pattern). JSON body,
unlike `/documents/upload`'s `multipart/form-data`, since this endpoint
takes no file:

**Request:**

```json
{
  "query": "How does a wind turbine generate electricity?",
  "top_k": 5,
  "similarity_threshold": 0.75,
  "document_id": null
}
```

**Response** — `200`:

```json
{
  "query": "How does a wind turbine generate electricity?",
  "results": [
    {
      "chunk_id": "b1e2...",
      "document_id": "3fa1...",
      "filename": "wind-report.pdf",
      "chunk_index": 4,
      "content": "Wind turbines convert kinetic energy...",
      "similarity": 0.87
    }
  ]
}
```

An empty `results` list (not an error) is a valid `200` response when
nothing clears the similarity threshold, or when no chunk in scope has an
embedding yet.

A separate backfill mechanism (§4) is exposed for testing too — exact
shape (a `POST /documents/backfill-embeddings` route vs. a standalone
script, matching Stage 18's `verify_persistence.py`/Stage 19's
`verify_persistence_api.py` precedent for one-off operational scripts) is
an implementation-time decision, not fixed by this spec.

### Error cases

| Scenario | Code | Detail (hand-written, static) |
|---|---|---|
| Missing/malformed request body | `422` | (automatic, via FastAPI/Pydantic) |
| Empty or whitespace-only `query` | `400` | `"Query text cannot be empty"` |
| `top_k` not a positive integer | `400` | `"top_k must be a positive integer"` |
| `similarity_threshold` outside `[0, 1]` | `400` | `"similarity_threshold must be between 0 and 1"` |
| `document_id` given but does not exist | `404` | `"No document found for this document_id"` |
| Embedding the query fails (OpenAI API error) | `500` | `"Something went wrong while processing this search. Please try again."` |
| Unexpected DB error | `500` | `"Something went wrong while processing this search. Please try again."` |

Same philosophy as every prior FastAPI stage: a short, static, hand-
written `detail` string on every `HTTPException`; the real exception
printed server-side, never echoed to the client; the existing global
`@app.exception_handler(Exception)` still applies as defense-in-depth.

---

## 11. Error Handling (pipeline-level, beyond the API table above)

- **Embedding generation failure during upload** (OpenAI API error while
  embedding a newly-chunked document): the whole upload fails (`500`,
  matching Stage 20's existing "DB write failed" case) rather than
  silently storing chunks with `embedding = NULL` — a partially-embedded
  document would be confusing to reason about later. Consistent with
  Stage 20's single-transaction-per-upload design (§5 of the Stage 20
  spec): the transaction should not commit unless embeddings for every
  chunk succeeded.
- **Backfill partial failure**: if embedding one chunk fails mid-backfill
  (e.g. a transient API error), already-embedded chunks in that batch
  keep their embeddings (each chunk's `UPDATE` commits independently) —
  a retry of the backfill only needs to touch chunks still `NULL`, not
  redo the whole batch. This is the opposite transaction shape from
  upload (§ above) deliberately: backfill is idempotent and resumable by
  nature (`WHERE embedding IS NULL`), so an all-or-nothing transaction
  would only make a partial run harder to recover from, not safer.
- **Searching before pgvector is enabled** (extension missing/DB
  misconfigured): surfaces as the generic `500` unexpected-DB-error case
  above — not a special-cased error, since this should only ever happen
  from an incomplete environment setup, not a normal runtime condition.

---

## 12. Testing Requirements

Following this project's standalone-script convention (asserts + prints,
no pytest, `python x_test.py` directly):

- Upload a document (via Stage 21's own `/documents/upload`) and confirm
  every resulting `document_chunks` row has a non-null `embedding` of the
  expected length (1536).
- Insert a `document_chunks` row directly with `embedding = NULL`
  (simulating a pre-Stage-21 row), run the backfill mechanism, confirm
  its `embedding` is populated afterward.
- Search with a query semantically related to an uploaded document's
  content and confirm the relevant chunk is returned and ranks above
  unrelated chunks.
- Confirm `top_k` is respected — a search returns at most `top_k` results
  even when more chunks exist.
- Confirm `similarity_threshold` excludes low-similarity results —
  search the same query with a very high threshold and confirm fewer (or
  zero) results come back.
- Confirm `document_id` scoping — a search restricted to one document
  never returns chunks from another.
- Confirm an empty/unrelated-enough search (or an all-NULL-embedding
  scope) returns `200` with `results: []`, not an error.
- Error cases from §10's table: empty query, invalid `top_k`, invalid
  `similarity_threshold`, unknown `document_id`.

---

## 13. Explicitly Out of Scope

- **RAG answer generation** — nothing in this stage feeds search results
  into an LLM call or produces a natural-language answer.
- **Knowledge Agent integration** — `search_knowledge_base` is untouched;
  it still only searches the bundled `knowledge_base/*.md` files. This
  stage's search endpoint is separate and stands alone.
- **Changes to supervisor/specialist routing** — the Stage 16-20
  supervisor+critic graph and its three specialists are untouched.
- **Vector index tuning** (`ivfflat`/`hnsw`) — a sequential scan is used;
  indexing is a future optimization, not built here (§5).
- **Hybrid/keyword search, reranking** — pure cosine-similarity vector
  search only.
- **Re-embedding on chunk edit/delete** — there is no chunk-editing
  capability anywhere in this project; not applicable yet.
- **Authentication / user accounts / multi-tenant scoping** — matches
  every existing endpoint in this project having no auth.
- **Streaming or async embedding jobs** — embedding happens synchronously
  within the request (upload) or the backfill run; no background task
  queue is introduced.

---

## 14. Future Integration (Stage 22 preview)

Stage 22 is expected to turn this stage's search endpoint into an actual
retrieval capability the Knowledge Agent (or a new specialist) can use:

```text
POST /documents/search (this stage's endpoint, or the query it wraps)
     ↓
Exposed as a LangChain @tool, the same shape as search_knowledge_base
     ↓
Bound to the Knowledge Agent (or a new "Document Agent" specialist),
     widening what it can answer from beyond the bundled knowledge_base/*.md
     ↓
Supervisor/critic routing unchanged in principle - critic_node already
     judges question/answer pairs generically, without caring which
     specialist or which underlying retrieval source produced the answer
```

Because Stage 21's search logic already returns clean, structured results
(chunk content + similarity + source filename — the same shape
`search_knowledge_base` already returns, just sourced from Postgres
instead of an in-memory store), Stage 22 should be able to wrap it in a
`@tool` function with minimal new code, without touching this stage's
embedding/storage/search implementation at all.
