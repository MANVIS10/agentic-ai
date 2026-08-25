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
    from app.db import init_schema

    await init_schema()
    await init_schema()  # second call must not raise
