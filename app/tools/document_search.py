"""The Knowledge Agent's uploaded-document search tool, moved from
stage25_react_ui/backend/main.py (lines 214-259). `pg_conn` (a bare
module-level connection in the original) becomes `get_connection()`;
`embeddings` and the untrusted-content envelope constants move to their
respective modules but keep their exact values and behavior.

search_uploaded_documents uses InjectedState("user_id") - a string key -
so it does not import KnowledgeState, matching the original.
"""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pgvector import Vector

from app.config import KNOWLEDGE_TOOL_K, UNTRUSTED_CONTENT_PREFIX, UNTRUSTED_CONTENT_SUFFIX
from app.db import get_connection
from app.llm import embeddings


@tool
def search_uploaded_documents(
    query: str,
    user_id: Annotated[str, InjectedState("user_id")],
) -> str:
    """Search documents the user has uploaded for information relevant to
    a natural-language question and return the most relevant text chunks.

    Use this for any question that might be answered by a document the
    user has uploaded. There is no other knowledge source available.
    """
    try:
        query_embedding = embeddings.embed_query(query)
    except Exception as exc:
        print(f"[search_uploaded_documents] Embedding error: {exc}")
        return "Something went wrong while searching uploaded documents."

    try:
        rows = get_connection().execute(
            """
            SELECT dc.content, d.filename
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.embedding IS NOT NULL
              AND d.user_id = %s
            ORDER BY dc.embedding <=> %s
            LIMIT %s
            """,
            (user_id, Vector(query_embedding), KNOWLEDGE_TOOL_K),
        ).fetchall()
    except Exception as exc:
        print(f"[search_uploaded_documents] DB error: {exc}")
        return "Something went wrong while searching uploaded documents."

    # No similarity threshold is applied, matching search_knowledge_base's
    # own threshold-free k=3 search (unchanged from Stage 22/23). Without
    # one, ORDER BY ... LIMIT always returns the closest k chunks whenever
    # ANY embedded chunk owned by this user_id exists, so the only reachable
    # empty case is "this user has no uploaded chunks with an embedding at
    # all" - not "documents exist but none are relevant".
    if not rows:
        return "No documents have been uploaded yet."

    formatted = [f"[source: {filename}]\n{content}" for content, filename in rows]
    body = "\n\n".join(formatted)
    return f"{UNTRUSTED_CONTENT_PREFIX}{body}{UNTRUSTED_CONTENT_SUFFIX}"
