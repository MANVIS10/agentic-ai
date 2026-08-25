"""Database connection pool, schema setup, and checkpointer.

Phase 2 (async conversion): replaces Phase 1's single shared psycopg
connection with an `AsyncConnectionPool`. The old `get_connection()`
returned ONE connection shared by every request; since Phase 1's endpoints
were sync `def`, FastAPI ran them in a 40-thread pool, so `with
conn.transaction():` in one thread could capture another thread's
concurrent `execute()` - transactions are per-CONNECTION, not per-thread.
A pool gives each caller its own connection for the life of its work, so
`async with conn.transaction():` is genuinely isolated.

`get_pool()` opens the pool lazily (not at import), matching Phase 1's
"import must never need a live database" convention. `connection()` is the
async context manager every caller uses to borrow one pooled connection.
`init_schema()` and `get_checkpointer()` are now async/loop-bound - see
their docstrings.
"""

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from app.config import settings

_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None

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


async def _configure(conn) -> None:
    """Registers pgvector's `vector` type on every pooled connection as it's
    created. The old single-connection code called `register_vector(conn)`
    once, on its one connection (init_schema(), main.py:799-ish); with a
    pool handing out many different connections over the process lifetime,
    that registration has to happen per-connection, via the pool's
    `configure` hook, or a connection opened after startup would not know
    the `vector` type and document search would fail unpredictably."""
    await register_vector_async(conn)


def get_pool() -> AsyncConnectionPool:
    """Replaces Phase 1's single shared connection. Each caller gets its own
    connection for the duration of its work, so `async with
    conn.transaction()` is genuinely isolated - the bug this phase fixes.
    Opened lazily (`open=False` + explicit `.open()` in `connection()`), not
    at import or at construction, so importing this module never needs a
    live database.
    """
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.db_pool_max_size,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            configure=_configure,
        )
    return _pool


async def _ensure_pool_open() -> AsyncConnectionPool:
    """Opens the pool on first use if it isn't already open. Shared by
    `connection()` and `init_schema()`: the checkpointer's own `setup()`
    borrows a connection directly from the pool object (bypassing
    `connection()` entirely, since AsyncPostgresSaver is handed the raw pool
    in `get_checkpointer()`), so `init_schema()` must ensure the pool is
    open itself before calling `checkpointer.setup()` - otherwise the very
    first caller of `init_schema()` (before anything has gone through
    `connection()`) hits `PoolClosed: not open yet`.
    """
    pool = get_pool()
    if pool.closed:
        await pool.open()
    return pool


@asynccontextmanager
async def connection():
    """Async context manager yielding a pooled `AsyncConnection`. Opens the
    pool on first use if it isn't already open."""
    pool = await _ensure_pool_open()
    async with pool.connection() as conn:
        yield conn


async def close_pool() -> None:
    """Closes the pool, for the lifespan shutdown handler.

    Also resets `_checkpointer` to None, not just `_pool`. AsyncPostgresSaver
    captures `asyncio.get_running_loop()` (and creates an `asyncio.Lock()`)
    at CONSTRUCTION time (`get_checkpointer()`), so a checkpointer built
    against one event loop is permanently bound to it. If a caller closes
    the pool (e.g. one process/loop shutting down) but this module's
    `_checkpointer` singleton were left in place, the NEXT `get_checkpointer()`
    call - potentially on an entirely different event loop, as happens
    between two independent test-suite TestClient instances, each running
    its own ASGI lifespan on its own anyio blocking-portal thread/loop -
    would silently hand back a checkpointer wired to a dead loop, which
    hangs (rather than raising) the moment anything tries to use its
    loop-bound lock. Resetting both together keeps "pool" and "checkpointer
    built from that pool" from ever drifting out of sync.
    """
    global _pool, _checkpointer
    if _pool is not None:
        await _pool.close()
        _pool = None
    _checkpointer = None


def get_checkpointer() -> AsyncPostgresSaver:
    """The async counterpart of the original's module-level PostgresSaver,
    now backed by the connection pool rather than a single connection.
    AsyncPostgresSaver captures the running event loop at construction time
    (`asyncio.get_running_loop()`), so this must be called from within
    async code with a loop already running - the lifespan handler, not
    module import.
    """
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = AsyncPostgresSaver(get_pool())
    return _checkpointer


async def init_schema() -> None:
    """Runs checkpointer.setup(), the documents/document_chunks DDL, and
    the pgvector setup - everything the original ran unconditionally at
    module scope. Idempotent: every statement here is CREATE ... IF NOT
    EXISTS / ADD COLUMN IF NOT EXISTS, so calling this more than once is
    safe, matching the original's own behavior (it ran on every process
    start too). Now async since it awaits both the checkpointer's async
    setup() and every query over the pool.
    """
    await _ensure_pool_open()  # checkpointer.setup() borrows from the pool directly - see _ensure_pool_open's docstring
    await get_checkpointer().setup()  # idempotent: creates the checkpoint tables on first run only

    async with connection() as conn:
        await conn.execute(DOCUMENTS_TABLE_SQL)
        await conn.execute(DOCUMENT_CHUNKS_TABLE_SQL)  # after documents - it has a FK reference to it

        await conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default-user'"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents (user_id)"
        )

        # -------------------------------------------------------------------
        # pgvector setup (Stage 21-23, unchanged). Idempotent, same "safe to
        # run every process start" convention as checkpointer.setup() and
        # the DDL above. register_vector itself now happens per-connection
        # via the pool's configure hook (_configure above), not here.
        # -------------------------------------------------------------------
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(1536)"
        )
