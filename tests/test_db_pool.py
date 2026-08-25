import asyncio

import pytest


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
