"""Database connection, schema setup, and checkpointer, moved from
stage25_react_ui/backend/main.py (lines 742-799).

The original opened the connection and ran every DDL statement at module
scope, so `import main` was never safe without a live Postgres connection.
This module makes that lazy: `get_connection()` opens the connection on
first call (not at import), and `init_schema()` - not import - runs the
checkpointer setup, the documents/document_chunks DDL, and the pgvector
setup. Both remain idempotent, matching the original's "safe every process
start" convention.
"""

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from pgvector.psycopg import register_vector

from app.config import settings

_conn: psycopg.Connection | None = None
_checkpointer: PostgresSaver | None = None

# ---------------------------------------------------------------------------
# Document upload tables (Stage 20-23, unchanged schema). Created
# idempotently by init_schema(), the same "safe every process start"
# convention as checkpointer.setup() below.
# ---------------------------------------------------------------------------

DOCUMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'default-user',
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

DOCUMENT_CHUNKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def get_connection() -> psycopg.Connection:
    """One shared autocommit connection, exactly as the original had at
    main.py:749. Phase 2 replaces this with a ConnectionPool - the shared
    connection is a known concurrency hazard (a `with conn.transaction()`
    in one thread captures another thread's execute), preserved here
    deliberately because Phase 1 forbids behavior change."""
    global _conn
    if _conn is None:
        _conn = psycopg.connect(
            settings.database_url, autocommit=True, prepare_threshold=0
        )
    return _conn


def get_checkpointer() -> PostgresSaver:
    """The same PostgresSaver as the original's module-level `checkpointer`
    (main.py:750), lazily constructed over get_connection()'s connection."""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = PostgresSaver(get_connection())
    return _checkpointer


def init_schema() -> None:
    """Runs checkpointer.setup(), the documents/document_chunks DDL, and
    the pgvector setup - everything the original ran unconditionally at
    module scope (main.py:751, 783-799). Idempotent: every statement here
    is CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS, so calling this
    more than once is safe, matching the original's own behavior (it ran
    on every process start too).
    """
    conn = get_connection()

    # Same checkpointer setup as Stage 23: a PostgresSaver backed by a real
    # database, so this graph's checkpoints (the plan, subtasks,
    # results-so-far, and any paused-at-human_approval interrupt) outlive
    # the Python process.
    get_checkpointer().setup()  # idempotent: creates the checkpoint tables on first run only

    conn.execute(DOCUMENTS_TABLE_SQL)
    conn.execute(DOCUMENT_CHUNKS_TABLE_SQL)  # after documents - it has a FK reference to it

    conn.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default-user'"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents (user_id)")

    # ---------------------------------------------------------------------
    # pgvector setup (Stage 21-23, unchanged). Idempotent, same "safe to
    # run every process start" convention as checkpointer.setup() and the
    # DDL above.
    # ---------------------------------------------------------------------
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    register_vector(conn)
