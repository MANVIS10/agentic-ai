"""GET /documents, POST /documents/upload, /documents/backfill-embeddings,
/documents/search, moved verbatim from stage25_react_ui/backend/main.py
(lines 1435-1745).

`UNSUPPORTED_TYPE_DETAIL` (original main.py:1435) lives here rather than in
app/config.py - it's a single string used only by this router, not a
cross-cutting constant the way the other config values are.

Per the Tasks 1-6 handoff: app/ingestion/store.py's `embed_and_store`
deliberately does not open its own transaction or convert exceptions to
HTTPException. The original's atomicity came from `upload_document`
wrapping BOTH the `INSERT INTO documents` row and the chunk inserts in one
`with pg_conn.transaction():` block (main.py:1550-1578) - this router
reproduces that by opening the transaction here, inserting the documents
row, and calling `embed_and_store` inside the same block, with the
try/except -> 500 conversion at this route layer.

Phase 2 (async conversion): every handler is now `async def`, using the
pooled `connection()` async context manager (app.db, Task 1) instead of
the old single shared sync connection - each request now genuinely gets
its own connection, so the upload transaction below is finally isolated
from a concurrent request's writes. extract_text_with_timeout now raises
`TimeoutError` (== `asyncio.TimeoutError` on this Python version) on
timeout instead of `concurrent.futures.TimeoutError`, since it runs the
CPU-bound extraction via `asyncio.to_thread` + `asyncio.wait_for` rather
than a raw ThreadPoolExecutor.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pgvector import Vector

from app.api.schemas import (
    BackfillResponse,
    DocumentListResponse,
    DocumentSummary,
    SearchRequest,
    SearchResponse,
    SearchResult,
    UploadResponse,
)
from app.config import (
    BACKFILL_BATCH_SIZE,
    CORRUPT_FILE_DETAIL,
    EXTRACTION_TIMEOUT_SECONDS,
    LIST_IP_RATE_LIMIT,
    LIST_USER_RATE_LIMIT,
    MAX_FILE_SIZE_BYTES,
    MAX_FILENAME_LENGTH,
    MAX_TEXT_INPUT_LENGTH,
    MAX_TOP_K,
    SEARCH_IP_RATE_LIMIT,
    SEARCH_USER_RATE_LIMIT,
    UPLOAD_IP_RATE_LIMIT,
    UPLOAD_USER_RATE_LIMIT,
)
from app.db import connection
from app.security.auth import current_user_id, require_admin
from app.ingestion.extract import extract_text_with_timeout, get_file_type
from app.ingestion.store import chunk_text, embed_and_store
from app.llm import embeddings
from app.security.ratelimit import enforce_rate_limits
from app.security.validation import validate_text_field

logger = logging.getLogger(__name__)

router = APIRouter()

UNSUPPORTED_TYPE_DETAIL = "Unsupported file type. Allowed types: pdf, txt, docx"


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    http_request: Request,
    user_id: str = Depends(current_user_id),
):
    """List documents belonging to user_id, most recently uploaded first
    (spec §3.1, confirmed addition). Same WHERE user_id = %s isolation
    filter Stage 23 already applies on every other retrieval path, same
    _validate_text_field/_enforce_rate_limits pattern every other
    user_id-scoped route already uses. A read-only query against an
    existing table - not a new capability. An empty list is a valid 200,
    not an error.

    `user_id` now comes from the bearer token rather than a query string, so
    the WHERE clause below finally filters on an identity the server
    established instead of one the caller asserted.
    """
    client_ip = http_request.client.host if http_request.client else "unknown"
    await enforce_rate_limits(
        "list", user_id, client_ip, LIST_USER_RATE_LIMIT, LIST_IP_RATE_LIMIT
    )

    async with connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT id, filename, file_type, chunk_count, uploaded_at "
                "FROM documents WHERE user_id = %s ORDER BY uploaded_at DESC",
                (user_id,),
            )
        ).fetchall()
    return DocumentListResponse(
        documents=[
            DocumentSummary(
                document_id=str(row[0]),
                filename=row[1],
                file_type=row[2],
                chunk_count=row[3],
                created_at=row[4].isoformat(),
            )
            for row in rows
        ]
    )


@router.post("/documents/upload", response_model=UploadResponse)
async def upload_document(
    http_request: Request,
    file: UploadFile = File(...),
    # Still accepted so the current frontend keeps working during migration,
    # and deliberately ignored - ownership comes from the token below.
    user_id_form: str | None = Form(default=None, alias="user_id"),
    user_id: str = Depends(current_user_id),
):
    """Validate, extract, chunk, embed, and durably store an uploaded
    PDF/TXT/DOCX file, owned by the given user_id.

    Validation order: user_id -> rate limit -> filename length -> extension
    -> bounded read -> empty -> size limit -> extract-with-timeout
    (content-based check #2, plus the new PDF-page/DOCX-zip-bomb/timeout
    guards) -> empty-extracted-text -> chunk -> embed -> store.
    Error-handling style matches every other route: a short, hand-written
    detail string on every HTTPException, the real exception printed
    server-side, never echoed to the client.
    """

    client_ip = http_request.client.host if http_request.client else "unknown"
    await enforce_rate_limits(
        "upload", user_id, client_ip, UPLOAD_USER_RATE_LIMIT, UPLOAD_IP_RATE_LIMIT
    )

    filename = file.filename or ""
    if len(filename) > MAX_FILENAME_LENGTH:
        raise HTTPException(status_code=400, detail="filename is too long")

    file_type = get_file_type(filename)
    if file_type is None:
        raise HTTPException(status_code=400, detail=UNSUPPORTED_TYPE_DETAIL)

    # Bounded read, not read-then-check: reads at most one byte more than
    # the limit allows, so the server can never be made to buffer more
    # than MAX_FILE_SIZE_BYTES + 1 bytes regardless of what the client
    # actually sends. `file.read(...)` (UploadFile's own async method, not
    # the old `file.file.read(...)` on the raw sync file object) so a large
    # upload that Starlette has spooled to disk gets read via its own
    # threadpool-backed path instead of blocking this route's event loop
    # directly.
    file_bytes = await file.read(MAX_FILE_SIZE_BYTES + 1)

    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    try:
        text = await extract_text_with_timeout(file_bytes, file_type)
    except TimeoutError:
        print(
            f"[/documents/upload] Extraction timed out for {filename!r} "
            f"after {EXTRACTION_TIMEOUT_SECONDS}s"
        )
        raise HTTPException(status_code=422, detail=CORRUPT_FILE_DETAIL)
    except Exception as exc:
        print(f"[/documents/upload] Extraction failed for {filename!r}: {exc}")
        raise HTTPException(status_code=422, detail=CORRUPT_FILE_DETAIL)

    if not text.strip():
        raise HTTPException(
            status_code=422, detail="No extractable text found in this document"
        )

    chunks = chunk_text(text)
    document_id = uuid.uuid4()

    try:
        # conn is autocommit; .transaction() still gives an explicit
        # BEGIN/COMMIT/ROLLBACK block on it, so a failure partway through
        # (embedding OR chunk insertion) rolls back the whole thing - no
        # orphaned documents row with a wrong chunk_count or a partial
        # chunk set. See this module's docstring for why the embedding
        # step now runs inside this block rather than before it. A pooled
        # connection (Task 1) makes this isolation genuine: a concurrent
        # request's execute() can no longer land inside THIS transaction,
        # the transaction-interleaving bug this phase fixes.
        async with connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO documents (id, filename, file_type, file_size_bytes, chunk_count, user_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (document_id, filename, file_type, len(file_bytes), len(chunks), user_id),
                )
                await embed_and_store(conn, document_id, chunks)
    except Exception as exc:
        print(f"[/documents/upload] DB write failed for {filename!r}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while storing this document. Please try again.",
        )

    return UploadResponse(
        document_id=str(document_id),
        filename=filename,
        file_type=file_type,
        user_id=user_id,
        chunk_count=len(chunks),
        status="stored",
    )


@router.post("/documents/backfill-embeddings", response_model=BackfillResponse)
async def backfill_embeddings(_admin: str = Depends(require_admin)):
    """Embed every document_chunks row with embedding IS NULL - rows
    written before pgvector existed, or leftovers from a previous partial
    backfill run. Not user-scoped, by nature: it repairs rows across every
    tenant, which is precisely why it now requires an ADMIN-scoped token
    (app/security/auth.py). Until this phase it required nothing at all -
    an unauthenticated stranger could make the server embed every unembedded
    chunk in the database, spending the OpenAI budget at will.

    Each chunk is embedded and UPDATEd independently (one call per chunk,
    not one giant batch), each in its own try/except: one bad chunk never
    blocks the rest, every successful UPDATE commits immediately on this
    autocommit connection, and a retry of this endpoint only ever touches
    rows still NULL - it's naturally resumable without any extra state.

    Read in batches rather than one unbounded SELECT. The old query
    materialized every NULL-embedding row - id AND content - into memory at
    once before embedding any of them; on a database with a real backlog
    that is a large allocation held for the whole run. Since each successful
    UPDATE removes its row from the `embedding IS NULL` set, re-running the
    same LIMITed query walks the backlog without an OFFSET. Rows that FAIL
    stay NULL, so the loop skips past them by remembering how many failures
    it has seen and offsetting by exactly that - otherwise a single
    permanently-bad chunk would be re-fetched forever.
    """
    embedded_count = 0
    failed_count = 0
    chunks_found = 0

    async with connection() as conn:
        while True:
            rows = await (
                await conn.execute(
                    "SELECT id, content FROM document_chunks WHERE embedding IS NULL "
                    "ORDER BY id LIMIT %s OFFSET %s",
                    (BACKFILL_BATCH_SIZE, failed_count),
                )
            ).fetchall()
            if not rows:
                break
            chunks_found += len(rows)

            for chunk_id, content in rows:
                try:
                    chunk_embedding = (await embeddings.aembed_documents([content]))[0]
                    await conn.execute(
                        "UPDATE document_chunks SET embedding = %s WHERE id = %s",
                        (Vector(chunk_embedding), chunk_id),
                    )
                    embedded_count += 1
                except Exception:
                    logger.exception(
                        "[/documents/backfill-embeddings] Failed to embed chunk %s", chunk_id
                    )
                    failed_count += 1

    return BackfillResponse(
        chunks_found=chunks_found, embedded_count=embedded_count, failed_count=failed_count
    )


@router.post("/documents/search", response_model=SearchResponse)
async def search_documents(
    request: SearchRequest,
    http_request: Request,
    user_id: str = Depends(current_user_id),
):
    """Semantic similarity search over embedded document_chunks, scoped to
    the requesting user_id. Kept for direct testing/inspection of the
    search layer, independent of the Knowledge Agent's own tool
    (search_uploaded_documents), which calls the same underlying query
    in-process rather than this route. An empty results list is a valid
    200, not an error, whenever nothing clears similarity_threshold or no
    chunk owned by this user_id in scope has an embedding yet.

    document_id ownership: if request.document_id names a document owned
    by a DIFFERENT user_id, this returns the exact same 404 as a
    document_id that doesn't exist at all - never a distinct message or
    status code, so a caller can't use this endpoint to probe whether a
    given document_id belongs to someone else (unchanged from Stage 23).

    New in this stage: `query` now has a max-length cap (it already had an
    empty check) and `top_k` gets an upper bound in addition to its
    existing lower bound, plus rate limiting - all checked before the
    embedding call/DB query run.
    """

    client_ip = http_request.client.host if http_request.client else "unknown"
    await enforce_rate_limits(
        "search", user_id, client_ip, SEARCH_USER_RATE_LIMIT, SEARCH_IP_RATE_LIMIT
    )

    query_text = validate_text_field(
        request.query, "Query text", max_length=MAX_TEXT_INPUT_LENGTH
    )
    if request.top_k < 1:
        raise HTTPException(status_code=400, detail="top_k must be a positive integer")
    if request.top_k > MAX_TOP_K:
        raise HTTPException(status_code=400, detail=f"top_k must be at most {MAX_TOP_K}")
    if request.similarity_threshold is not None and not (
        0 <= request.similarity_threshold <= 1
    ):
        raise HTTPException(
            status_code=400, detail="similarity_threshold must be between 0 and 1"
        )

    async with connection() as conn:
        document_uuid = None
        if request.document_id is not None:
            try:
                document_uuid = uuid.UUID(request.document_id)
            except ValueError:
                raise HTTPException(
                    status_code=404, detail="No document found for this document_id"
                )
            exists = await (
                await conn.execute(
                    "SELECT 1 FROM documents WHERE id = %s AND user_id = %s",
                    (document_uuid, user_id),
                )
            ).fetchone()
            if exists is None:
                raise HTTPException(
                    status_code=404, detail="No document found for this document_id"
                )

        try:
            query_embedding = await embeddings.aembed_query(query_text)
        except Exception as exc:
            print(f"[/documents/search] Embedding query failed: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong while processing this search. Please try again.",
            )

        # A subquery, not a flat SELECT: Postgres won't let a WHERE clause
        # reference a SELECT-list alias in the same query, but an OUTER query
        # can reference an inner query's output column by name - so `similarity`
        # is computed once in the inner SELECT and reused by both the outer
        # WHERE (similarity_threshold) and ORDER BY, instead of recomputing the
        # <=> operator a second time. Only ever appends pre-written static
        # clause text based on which optional filters are present - every
        # actual value still goes through the params list below, never
        # string-interpolated. d.user_id = %s is unconditional (every search is
        # scoped to a user), unlike the optional dc.document_id filter below it.
        sql = """
            SELECT * FROM (
                SELECT dc.id AS chunk_id, dc.document_id, dc.chunk_index, dc.content,
                       d.filename, 1 - (dc.embedding <=> %s) AS similarity
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE dc.embedding IS NOT NULL
                  AND d.user_id = %s
        """
        params = [Vector(query_embedding), user_id]
        if document_uuid is not None:
            sql += " AND dc.document_id = %s"
            params.append(document_uuid)
        sql += ") sub"
        if request.similarity_threshold is not None:
            sql += " WHERE similarity >= %s"
            params.append(request.similarity_threshold)
        sql += " ORDER BY similarity DESC LIMIT %s"
        params.append(request.top_k)

        try:
            rows = await (await conn.execute(sql, params)).fetchall()
        except Exception as exc:
            print(f"[/documents/search] DB query failed: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong while processing this search. Please try again.",
            )

    results = [
        SearchResult(
            chunk_id=str(chunk_id),
            document_id=str(row_document_id),
            chunk_index=chunk_index,
            content=content,
            filename=filename,
            similarity=similarity,
        )
        for chunk_id, row_document_id, chunk_index, content, filename, similarity in rows
    ]
    return SearchResponse(query=request.query, results=results)
