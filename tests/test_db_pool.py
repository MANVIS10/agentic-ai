import asyncio

import pytest


@pytest.fixture(autouse=True)
async def _close_pool_after_each_test():
    """These tests exercise app.db's pool directly, on pytest-asyncio's
    session event loop (no TestClient involved) - binding the singleton
    pool to that loop. A later test module's own TestClient runs its ASGI
    lifespan (including its own init_schema()/close_pool()) on a SEPARATE
    event loop (anyio's blocking portal always uses its own background
    thread + loop): if a pool opened here were left open, that TestClient's
    lifespan would inherit this already-open, wrong-loop-bound pool instead
    of opening its own, and fail with "Task ... attached to a different
    loop" at its own teardown. Closing after every test here means whatever
    runs next - on whatever loop - always starts from a clean slate.
    """
    yield
    from app.db import close_pool

    await close_pool()


async def test_concurrent_transactions_do_not_interleave(pg_available):
    """The bug this phase fixes: with one shared connection, a rollback in
    one request could discard another request's concurrent write. With a
    pool, each gets its own connection, so a failure is isolated."""
    from app.db import connection

    async def failing_write():
        try:
            async with connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "CREATE TEMP TABLE IF NOT EXISTS t_iso (v int)"
                    )
                    raise RuntimeError("boom")
        except RuntimeError:
            pass

    async def good_write():
        async with connection() as conn:
            row = await (await conn.execute("SELECT 1")).fetchone()
            return row[0]

    results = await asyncio.gather(failing_write(), good_write(), good_write())
    assert results[1] == 1 and results[2] == 1


async def test_pool_serves_concurrent_callers(pg_available):
    from app.db import connection

    async def ping():
        async with connection() as conn:
            return (await (await conn.execute("SELECT 1")).fetchone())[0]

    assert await asyncio.gather(*(ping() for _ in range(10))) == [1] * 10
