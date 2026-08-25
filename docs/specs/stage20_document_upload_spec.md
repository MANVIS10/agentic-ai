# Stage 20 Specification — User Document Upload & Ingestion

## 0. Status

```text
Stage 18  Postgres persistence     ✅ (deliberate extension, post-roadmap)
Stage 19  FastAPI HTTP backend     ✅ (deliberate extension, post-roadmap)
Stage 20  Document upload/ingest   → Next (this spec)
Stage 21  Embeddings + retrieval   ⏳ (future, out of scope here)
```

Like Stage 18 and 19, Stage 20 is a deliberate extension past the original
roadmap in `spec_document.md`, not a numbered item from it. It builds on
Stage 19's FastAPI app (`stages/stage19_fastapi_backend/`) the same way Stage 19
built on Stage 18: a new stage folder that duplicates what it needs rather
than editing the previous stage in place, per this project's "previous
stages left untouched, no shared `common/` module" rule (`CLAUDE.md`).

This document is a **specification only**. No implementation code is
written against it yet — see `docs/plans/` for where the implementation
plan will go once this spec is approved.

---

## 1. Purpose

Every retrieval capability built so far (Stage 3's `search_knowledge_base`)
answers questions only from documents the *project* shipped with
(`knowledge_base/*.md`, bundled at build time). There is no way for a
person using the API to hand the system *their own* document and have it
become part of what an agent can answer questions about.

Stage 20 closes that gap on the ingestion side only: it lets a user upload
a PDF, TXT, or DOCX file over HTTP, validates it, extracts its text,
chunks that text the same way Stage 3/8/10/17/18/19's knowledge-base
loader already does (`RecursiveCharacterTextSplitter`), and persists the
chunks durably in the same PostgreSQL database Stage 18/19 already run
(`docker-compose.yml`, root `DATABASE_URL`).

This is deliberately **storage only**. It does not make those chunks
searchable yet. The reason to build it as its own stage, before touching
retrieval, is the same reason Stage 18 isolated durable checkpointing from
Stage 19's HTTP layer: each stage should isolate one new concept so it can
be read, tested, and understood on its own. Here, the new concept is
*taking arbitrary user-supplied binary/text input through validation,
extraction, and chunking into durable storage* — independent of whatever
later reads that storage back out.

**How this eventually helps the Knowledge Agent:** once Step 21 adds
embeddings and a retrieval path over the `document_chunks` table this
stage creates, the Knowledge Agent (currently scoped to the bundled
`knowledge_base/*.md` files only, per `CLAUDE.md`'s "one tool per stage"
rule and Stage 3/8/16/17/18/19's `search_knowledge_base` tool) will be
able to search user-uploaded documents too, without this stage's ingestion
pipeline needing to change at all — the same "pluggable backend behind a
stable interface" separation Stage 18 proved for checkpointers.

---

## 2. Scope of Step 20

In scope:

- File upload endpoint accepting one file per request.
- Supported formats: **PDF**, **TXT**, **DOCX**.
- File validation: extension/content-type check, empty-file check,
  file-size limit.
- Text extraction per format (reusing `pypdf` for PDF, per Stage 5;
  a new `python-docx` dependency for DOCX; plain decode for TXT).
- Chunking via `RecursiveCharacterTextSplitter` (same library Stage
  3/8/10/16/17/18/19 already use for the bundled knowledge base).
- Durable persistence of the document and its chunks to PostgreSQL (the
  same database Stage 18/19 already run via `docker-compose.yml`) —
  **not** the in-process `InMemoryVectorStore` Stage 3's knowledge base
  uses, since upload data must survive a process restart the same way
  Stage 18 made checkpoints survive one.

Explicitly not in scope — see §9.

---

## 3. API

### `POST /documents/upload`

Adds one new route to the existing Stage 19 FastAPI app
(`stages/stage19_fastapi_backend/main.py`'s pattern: Pydantic response models,
hand-written `HTTPException` detail strings, no raw exception text
returned to the client — same conventions as `/chat`, `/approve`,
`/reject`).

**Request** — `multipart/form-data`, not JSON (the existing three
endpoints are JSON bodies, but a binary file upload needs `multipart/
form-data`; this requires adding `python-multipart` to `requirements.txt`,
FastAPI's documented dependency for `UploadFile`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | The PDF/TXT/DOCX file itself |

No `thread_id` — uploaded documents are not scoped to a conversation
thread in this stage (see §9, "Knowledge Agent integration" is out of
scope, so there is nothing yet that would consume a thread-scoped
document).

**Response** — `200`, JSON:

```json
{
  "document_id": "3fa1c2e4-...",
  "filename": "report.pdf",
  "file_type": "pdf",
  "chunk_count": 14,
  "status": "stored"
}
```

**Error cases:**

| Scenario | Code | Detail (hand-written, static) |
|---|---|---|
| No file included in the request | `422` | (automatic, via FastAPI/Pydantic — same as a malformed `/chat` body today) |
| Unsupported file extension/content-type | `400` | `"Unsupported file type. Allowed types: pdf, txt, docx"` |
| File exceeds the size limit | `413` | `"File exceeds the maximum allowed size of {N} MB"` |
| File is empty (0 bytes) | `400` | `"Uploaded file is empty"` |
| File extracts to no usable text (e.g. a scanned/image-only PDF) | `422` | `"No extractable text found in this document"` |
| Extraction/parsing failure (corrupt file) | `422` | `"Could not read this file — it may be corrupted or malformed"` |
| Unexpected error (DB write failure, etc.) | `500` | `"Something went wrong while storing this document. Please try again."` |

This mirrors Stage 19's existing philosophy exactly: the real exception is
printed server-side (`print(f"[/documents/upload] ...")`, matching
`/chat`/`/approve`/`/reject`'s `print(f"[/chat] Error ...")` pattern), the
client only ever sees a short static string, and the existing global
`@app.exception_handler(Exception)` net still applies as defense-in-depth.

---

## 4. Database Design

Two new tables, created the same way Stage 18 created its checkpoint
tables: idempotently, at module load (`CREATE TABLE IF NOT EXISTS`,
mirroring `checkpointer.setup()`'s "runs unconditionally, safe to call
every process start" convention) — not a migrations framework, to match
this project's minimal-dependencies rule.

### `documents`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` (primary key) | Generated server-side (`uuid4()`), returned as `document_id` |
| `filename` | `TEXT` | Original filename as uploaded (see §7 on sanitization) |
| `file_type` | `TEXT` | One of `pdf`, `txt`, `docx` |
| `file_size_bytes` | `INTEGER` | Size of the raw upload |
| `chunk_count` | `INTEGER` | Number of rows written to `document_chunks` for this document |
| `uploaded_at` | `TIMESTAMPTZ` | `DEFAULT now()` |

### `document_chunks`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` (primary key) | Generated server-side |
| `document_id` | `UUID` | Foreign key → `documents.id`, `ON DELETE CASCADE` |
| `chunk_index` | `INTEGER` | 0-based position within the document, preserves chunk ordering |
| `content` | `TEXT` | The chunk's extracted text |
| `created_at` | `TIMESTAMPTZ` | `DEFAULT now()` |

**Relationship:** one `documents` row → many `document_chunks` rows
(one-to-many via `document_id`). `chunk_index` plus `document_id` is
enough to reconstruct a document's chunks in original order later —
important since Step 21's retrieval step will need to know which chunks
came from which document and in what sequence, without this stage having
to guess at Step 21's access pattern.

No `embedding` column in this stage — see §9.

---

## 5. Processing Pipeline

```text
Upload (multipart/form-data, POST /documents/upload)
   ↓
Validate (extension/content-type, non-empty, size limit)
   ↓
Extract text (pypdf for PDF · python-docx for DOCX · decode for TXT)
   ↓
Chunk (RecursiveCharacterTextSplitter — same library, same defaults
       family as Stage 3/8/10/16/17/18/19's knowledge-base loader)
   ↓
Store in PostgreSQL (one documents row + N document_chunks rows,
                      written in one transaction)
```

Each stage of the pipeline fails independently with its own error case
(§3) — an extraction failure never reaches the chunking step, an empty
extraction result never reaches storage, matching the "tool fails
gracefully" principle already established in Stage 4/5/15 (return/raise a
clear error instead of crashing or silently storing garbage).

The `documents` row and all of its `document_chunks` rows are written in a
single database transaction, so a failure partway through chunk insertion
never leaves an orphaned `documents` row with a wrong `chunk_count` or a
partial chunk set.

---

## 6. Chunking Behavior

- Reuses `langchain_text_splitters.RecursiveCharacterTextSplitter`, the
  same class already used by every knowledge-base loader in this repo
  (Stage 3/8/10/16/17/18/19), rather than introducing a second chunking
  approach.
- **Configurable chunk size and overlap** — module-level constants (e.g.
  `CHUNK_SIZE`, `CHUNK_OVERLAP`), not hardcoded inline, so they can be
  tuned without hunting through the pipeline code. Proposed defaults match
  the existing knowledge-base loader's own values (`chunk_size=400,
  chunk_overlap=50`) for consistency with the rest of the project, to be
  confirmed at implementation time rather than treated as fixed here.
- **Chunk ordering is preserved** via `chunk_index` (§4), assigned in the
  same order `RecursiveCharacterTextSplitter.split_text(...)` returns
  chunks — no reordering or deduplication applied.

---

## 7. Security and Validation Considerations

- **Allowed file types**, enforced by *content*, not filename alone where
  practical: `.pdf`/`.txt`/`.docx` extension is checked, and for PDF/DOCX
  the extraction step itself acts as a second check (a file with a `.pdf`
  extension that isn't actually a valid PDF fails at the "extraction/
  parsing failure" error case in §3, not silently accepted).
- **Empty or unreadable documents** are rejected explicitly (§3's `400`
  for zero-byte files, `422` for files that parse but extract to no
  usable text) rather than silently stored as a `documents` row with zero
  chunks.
- **Reasonable file-size limit**, enforced before extraction runs (reading
  `UploadFile`'s size, or capping bytes read) — proposed default in the
  low tens of MB, matching the kind of limit already implicit in Stage
  4/5's `MAX_CHARS = 4000` truncation philosophy (bound the work done on
  arbitrary external input), exact number to be confirmed at
  implementation time.
- **Filename handling**: the original filename is stored as metadata
  (`documents.filename`) for display purposes only — it is never used to
  construct a filesystem path (nothing in this pipeline writes the
  uploaded file to disk; extraction happens entirely in memory via
  `io.BytesIO`, the same pattern Stage 5's `fetch_pdf` already uses for
  downloaded PDF bytes), so path-traversal via a crafted filename isn't a
  vector here. No execution of uploaded content at any point — every
  format is read as data (text extraction), never executed or evaluated.

---

## 8. Testing Requirements

Following this project's existing test-file convention (a standalone
script run directly with `python`, asserts + prints, no pytest dependency
— see `CLAUDE.md`, and Stage 19's
`test_fastapi_backend.py`/`FastAPI TestClient` pattern):

- Upload a valid **PDF** → `200`, `chunk_count > 0`, rows exist in both
  tables.
- Upload a valid **TXT** → same.
- Upload a valid **DOCX** → same.
- Upload an **empty document** (0 bytes) → `400`, no rows written.
- Upload an **unsupported file type** (e.g. `.exe`, `.jpg`) → `400`, no
  rows written.
- Upload a document large enough to produce **multiple chunks** → confirm
  `chunk_count` matches the actual number of `document_chunks` rows, and
  that `chunk_index` values are contiguous and in original order.
- **Database persistence**: after upload, query `documents` and
  `document_chunks` directly (via `psycopg`, same style as Stage 18's
  `verify_persistence.py` querying `checkpoints`) to confirm the row data
  matches the API response (`document_id`, `filename`, `file_type`,
  `chunk_count`).
- (Recommended, matching Stage 19's error-case coverage) a corrupt/
  malformed file with a valid extension → `422`, no rows written.

---

## 9. Explicitly Out of Scope

- **Embeddings** — no `OpenAIEmbeddings` call, no `embedding` column in
  `document_chunks`. Chunks are stored as plain text only.
- **Vector database / vector search** — `document_chunks` is a plain
  Postgres table, not a vector store; no `pgvector` extension or similar
  is added in this stage.
- **RAG retrieval** — nothing in this stage reads `document_chunks` back
  out for answering questions.
- **Knowledge Agent integration** — `search_knowledge_base` (Stage 3 and
  every stage since that reuses it) is untouched; it still only searches
  the bundled `knowledge_base/*.md` files.
- **Changes to supervisor/specialist routing** — the Stage 16/17/18/19
  supervisor+critic graph and its three specialists are untouched; this
  stage adds a plain HTTP route, not a new graph node or tool.
- **Authentication / user accounts** — `POST /documents/upload` has no
  auth, matching every existing Stage 19 endpoint (none of `/chat`,
  `/approve`, `/reject` have auth either); documents are not scoped to a
  user or account, only to their own `document_id`.

---

## 10. Future Integration (Step 21 preview)

Step 21 is expected to add embeddings and retrieval over the
`document_chunks` table this stage creates:

```text
document_chunks (this stage)
     ↓
Embed each chunk's content (OpenAIEmbeddings, same model already used
     by search_knowledge_base — text-embedding-3-small)
     ↓
Store/query vectors (either a new embedding column + pgvector, or an
     InMemoryVectorStore rebuilt from document_chunks at startup — the
     same open choice Stage 3 already made once for the bundled
     knowledge base, now revisited for durability since Stage 18
     established that durability matters for this project)
     ↓
Expose as a tool/retrieval path the Knowledge Agent (or a new specialist)
     can call — the same "pluggable backend behind a stable interface"
     shape Stage 18 proved for checkpointers
```

Because `document_chunks` already stores ordered, plain-text chunks tied
to a `document_id`, Step 21 should be able to build its embedding step
entirely on top of this stage's schema without needing to touch the
upload/validation/extraction/chunking pipeline at all — the same
separation of concerns Stage 18 achieved between "durability" and "graph
logic," and Stage 19 achieved between "HTTP transport" and "graph logic."
