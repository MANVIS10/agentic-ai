"""Chunking and the embed-and-persist step of the upload pipeline, moved
from the body inline in `upload_document`
(stage25_react_ui/backend/main.py, roughly lines 1534-1578).

Only the document_chunks half of that original block lives here:
`embed_and_store` takes `document_id` and `chunks` but not
filename/file_type/file_size_bytes/user_id, so it cannot also perform the
original's `INSERT INTO documents` (that row's columns need those fields).
The router (Phase 1 Task 7, api/routers/documents.py) keeps that insert and
is expected to call `embed_and_store` from WITHIN its own
`with conn.transaction():` block, wrapping both inserts in one transaction
- exactly the original's atomicity guarantee ("a failure partway through
chunk insertion rolls back the whole thing - no orphaned documents row"),
just spanning two call sites instead of one inline block. This function
does not catch exceptions or raise HTTPException itself (the original's
try/except + 500 response lived at the FastAPI route level,
main.py:1541-1548 and 1573-1578) - that error-to-HTTP mapping is the
router's responsibility, same layer it was in originally.
"""

import uuid

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pgvector import Vector

from app.config import CHUNK_OVERLAP, CHUNK_SIZE
from app.llm import embeddings

document_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
)


def chunk_text(text: str) -> list[str]:
    """Split extracted document text into chunks, using the same
    RecursiveCharacterTextSplitter/CHUNK_SIZE/CHUNK_OVERLAP as the original
    (main.py:822-826, 1534)."""
    return document_splitter.split_text(text)


def embed_and_store(conn, document_id, chunks: list[str]) -> int:
    """Embed every chunk in one batched call (main.py:1542,
    "runs entirely BEFORE the transaction ... opens, so a failure here
    leaves nothing written"), then insert each into document_chunks using
    `conn` (main.py:1556-1572). Returns the number of chunks stored.
    """
    chunk_embeddings = embeddings.embed_documents(chunks)

    for index, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO document_chunks (id, document_id, chunk_index, content, embedding) "
            "VALUES (%s, %s, %s, %s, %s)",
            (uuid.uuid4(), document_id, index, chunk, Vector(chunk_embeddings[index])),
        )

    return len(chunks)
