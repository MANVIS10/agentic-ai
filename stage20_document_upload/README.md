# Stage 20: Document Upload & Ingestion

## What was added

Stage 19's exact FastAPI app, plus one new endpoint: `POST
/documents/upload`. A user can now hand the system a PDF, TXT, or DOCX
file over HTTP and have it validated, text-extracted, chunked, and
durably stored in PostgreSQL — the same database Stage 18/19 already run.

Nothing about the existing graph, specialists, supervisor, critic,
planner, or human-approval flow changed. Every node, edge, tool, prompt,
and route in `main.py` other than the new upload pipeline is copied
verbatim from
[`stage19_fastapi_backend/main.py`](../stage19_fastapi_backend/main.py),
including its `PostgresSaver` checkpointer, per-thread_id locking, and
`/health` `/chat` `/approve` `/reject` routes.

This stage is deliberately **storage only**. See
[`.claude/spec/stage20_document_upload_spec.md`](../.claude/spec/stage20_document_upload_spec.md)
for the full spec. Embeddings, vector search, RAG retrieval, and
Knowledge Agent integration are explicitly out of scope here — a future
Step 21 is expected to build on top of the `document_chunks` table this
stage creates, without needing to touch this ingestion pipeline at all.

## New concept

Every prior stage that touched a document worked with content the
project already had access to — files bundled at build time
(`knowledge_base/*.md`, Stage 3 onward) or a URL fetched over HTTP
(`fetch_pdf`, Stage 5). Nothing so far accepted arbitrary binary/text
input handed to it by a caller. `upload_document()` does exactly that: it
reads raw bytes from a `multipart/form-data` request and has to validate
them without ever trusting the caller's claims about the file — the
extension is checked first, but extraction itself is the real test of
"is this actually a valid PDF/DOCX?" (a `.pdf`-named file that isn't a
real PDF fails inside `PdfReader(...)`, not silently).

The two new tables (`documents`, `document_chunks`) are also the first
tables in this repo created with raw hand-written SQL. Every earlier
Postgres table (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations`) is owned and created by `PostgresSaver.setup()`.
These two are created the same way — idempotently, at module load — but
by hand, since there's no library that owns "a table of uploaded
documents."

## Architecture

```
Client (curl / TestClient / browser)
      |  multipart/form-data (one file per request)
      v
FastAPI app (stage20_document_upload/main.py, uvicorn process)
      |  validate -> extract -> chunk -> INSERT (one transaction)
      v
PostgreSQL (docker-compose `postgres` service, host port 5433)
      documents            <--  1:N  -->  document_chunks
```

### Processing pipeline

```
Upload (multipart/form-data, POST /documents/upload)
   |
Validate (extension, non-empty, size limit)
   |
Extract text (pypdf for PDF - python-docx for DOCX - decode for TXT)
   |
Chunk (RecursiveCharacterTextSplitter, chunk_size=400, chunk_overlap=50 -
       same values load_knowledge_base() already uses for the bundled
       knowledge base)
   |
Store in PostgreSQL (one documents row + N document_chunks rows,
                      written in a single transaction)
```

Each stage fails independently with its own error case (see the table
below) — an extraction failure never reaches chunking, an empty
extraction result never reaches storage.

## Database schema

```sql
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
)

CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

One `documents` row maps to many `document_chunks` rows via
`document_id`; `chunk_index` preserves each chunk's original order. Both
`id` columns are generated in Python (`uuid.uuid4()`), not by Postgres —
matching how the rest of the app treats identifiers as app-level, not
DB-generated. No `embedding` column yet — see "Explicitly out of scope"
in the spec.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status": "ok", "database": "connected"}`, or `503` if Postgres is unreachable |
| POST | `/chat` | `{"question": str, "thread_id": str}` | `200` with `status: "awaiting_approval"`, the plan, and the approval prompt |
| POST | `/approve` | `{"thread_id": str}` | `200` with `status: "completed"`, `results`, and `final_answer` |
| POST | `/reject` | `{"thread_id": str}` | `200` with `status: "rejected"`, empty `results`/`final_answer` |
| POST | `/documents/upload` | `multipart/form-data`, field `file` | `200` with `document_id`, `filename`, `file_type`, `chunk_count`, `status: "stored"` |

`/documents/upload` takes no `thread_id` — uploaded documents aren't
scoped to a conversation thread in this stage (nothing yet consumes a
thread-scoped document; see the spec's "out of scope" section).

### Status codes for `/documents/upload`

| Scenario | Code | Detail |
|---|---|---|
| No `file` part in the request | `422` | automatic, via FastAPI/Pydantic |
| Unsupported file extension | `400` | `"Unsupported file type. Allowed types: pdf, txt, docx"` |
| File exceeds 20 MB | `413` | `"File exceeds the maximum allowed size of 20 MB"` |
| File is empty (0 bytes) | `400` | `"Uploaded file is empty"` |
| File extracts to no usable text | `422` | `"No extractable text found in this document"` |
| Extraction/parsing failure (corrupt file) | `422` | `"Could not read this file — it may be corrupted or malformed"` |
| Unexpected error (DB write failure, etc.) | `500` | `"Something went wrong while storing this document. Please try again."` |

Same philosophy as `/chat`/`/approve`/`/reject`: every `HTTPException`'s
`detail` is a short, static, hand-written string — never the raw
exception text. The real exception is printed server-side; the existing
global `@app.exception_handler(Exception)` still applies as
defense-in-depth.

## Example requests

```bash
curl -X POST http://127.0.0.1:8000/documents/upload \
  -F "file=@C:/path/to/report.pdf"
```

PowerShell:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/documents/upload `
  -Form @{ file = Get-Item "C:\path\to\report.pdf" }
```

## Design decisions

- **File type detected by extension, not `UploadFile.content_type`.**
  Multipart clients set `content_type` inconsistently (many send
  `application/octet-stream` for anything), so it isn't a reliable
  signal on its own. Extraction itself acts as the real, content-based
  second check — a mismatched or corrupt file fails inside `PdfReader`/
  `DocxDocument`/`.decode("utf-8")`, not silently.
- **One transaction per upload**, via `pg_conn.transaction()` (psycopg3's
  explicit `BEGIN`/`COMMIT`/`ROLLBACK` block, usable even on this
  already-autocommit connection). A failure partway through chunk
  insertion rolls back the whole thing, so there's never an orphaned
  `documents` row with a wrong `chunk_count` or a partial chunk set.
- **Sync `file.file.read()`, not `await file.read()`.** Keeps this a
  plain `def` route, consistent with every other route in this stage —
  FastAPI/Starlette run sync routes in a threadpool automatically, the
  same reasoning Stage 19 already documented for `/chat`/`/approve`/
  `/reject`.
- **20 MB in-memory size cap, enforced before extraction runs.** The
  whole file is read into memory (`file.file.read()`), never streamed to
  disk — appropriate at this size, and consistent with `fetch_pdf`
  (Stage 5) also working entirely via `io.BytesIO`, no temp files.
  Documented as a simplicity tradeoff, not tuned for large-file
  production use.
- **No raw-SQL table-creation precedent existed in this repo before this
  stage** — every earlier Postgres table is owned by `PostgresSaver`.
  `documents`/`document_chunks` use hand-written `CREATE TABLE IF NOT
  EXISTS` DDL instead, executed unconditionally at module load (same
  "safe to run every process start" convention as
  `checkpointer.setup()`), deliberately not a migrations framework.
- **Test fixtures need no new binary assets.** The PDF check reuses
  `stage5_pdf_fetch/test_fetch_pdf.py`'s exact fixture URL (fetched live
  via `requests` at test time); the DOCX check generates a file
  in-memory with `python-docx`'s own `Document()`/`.add_paragraph()`/
  `.save(io.BytesIO())`. Neither needed a fixture file committed to the
  repo.
- **No dedicated persistence-restart script**, unlike Stage 18/19.
  `documents`/`document_chunks` have no pause/resume semantics to
  demonstrate — the test file's direct `pg_conn` queries against those
  tables already prove durability.
- **Uploads are not scoped to a `thread_id` or a user.** Matches every
  existing endpoint in this stage having no authentication; out of scope
  per the approved spec.

## How to run

Start Postgres (from the repo root, if not already running):

```
docker compose up -d
```

Install dependencies (adds `python-docx`/`python-multipart` to the
existing set):

```
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the API:

```
python stage20_document_upload/main.py
```

or, for dev auto-reload (run from the repo root):

```
uvicorn stage20_document_upload.main:app --reload --port 8000
```

## Running the tests

```
python stage20_document_upload/test_document_upload.py
```

Covers, all through `fastapi.testclient.TestClient` (no running server
needed): a valid PDF upload (fetched live from the same fixture URL
`stage5_pdf_fetch`'s test uses); a valid TXT upload; a valid DOCX upload
(generated in-memory); an empty file (`400`, no row written); an
unsupported extension (`400`); a document long enough to force multiple
chunks (`chunk_count > 1`, checked against `CHUNK_SIZE`); a corrupt PDF
and a corrupt DOCX (`422` each); a whitespace-only TXT file with no
extractable text (`422`); an oversized file (`413`); and a request with
no `file` part at all (`422`, automatic). Every stored-document check
also queries `documents`/`document_chunks` directly to confirm the row
data matches the API response and that `chunk_index` values are
contiguous and in order.

## What changed compared with Stage 19

| | Stage 19 | Stage 20 |
|---|---|---|
| Routes | `/health` `/chat` `/approve` `/reject` | Same four, plus `POST /documents/upload` |
| New tables | — | `documents`, `document_chunks` (hand-written DDL) |
| New dependencies | — | `python-docx`, `python-multipart` |
| Request body types | JSON only | JSON (existing routes) + `multipart/form-data` (upload) |
| Graph logic (nodes, edges, prompts, tools) | — | Byte-identical to Stage 19 |
| New infrastructure | — | None — reuses the existing `docker-compose.yml` Postgres service |

Stage 18 proved a checkpointer is a pluggable backend behind
`compile(checkpointer=...)`. Stage 19 proved the graph doesn't care how
it's called (REPL, test script, or HTTP route). Stage 20 proves a FastAPI
app built around that graph can grow a completely unrelated capability —
accepting and durably storing arbitrary user file uploads — without
touching the graph, its checkpointer, or any of its existing routes at
all.
