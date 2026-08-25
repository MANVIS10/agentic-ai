import importlib


def test_import_does_not_connect(monkeypatch):
    """The original connected to Postgres at import time (main.py:749).
    The port must be import-safe: connecting happens in get_connection()."""
    import psycopg

    def explode(*a, **kw):
        raise AssertionError("connected at import time")

    monkeypatch.setattr(psycopg, "connect", explode)
    import app.db

    importlib.reload(app.db)  # must not raise


async def test_init_schema_is_idempotent(pg_available):
    """init_schema() is now async (Phase 2) - awaiting it (not just calling
    it) is the point of this test: an un-awaited call would silently return
    a coroutine object and never run the DDL, making this test pass without
    testing anything.
    """
    from app.db import close_pool, init_schema

    await init_schema()
    await init_schema()  # second call must not raise

    # Close the pool this test just opened before returning. This test runs
    # directly on pytest-asyncio's session event loop (no TestClient
    # involved), so app.db's singleton pool ends up bound to THAT loop. A
    # later test module's own TestClient runs its ASGI lifespan (including
    # its own init_schema()/close_pool()) on a SEPARATE event loop (anyio's
    # blocking portal always uses its own background thread + loop) - if
    # this test left the pool open, that TestClient's lifespan would reuse
    # this already-open, wrong-loop-bound pool instead of opening its own,
    # and fail with "Task ... attached to a different loop" the moment it
    # tries to close it at its own teardown. Closing here means the next
    # caller, on whatever loop it runs on, always starts from a clean slate.
    await close_pool()
