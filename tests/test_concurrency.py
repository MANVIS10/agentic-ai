import asyncio
import time

import httpx
import pytest

from app.main import app


@pytest.fixture(autouse=True)
async def _close_pool_after():
    """httpx.ASGITransport calls the app directly in-process on the CURRENT
    coroutine's own event loop - unlike starlette's TestClient, which always
    runs the app (including its lifespan) on a separate portal-thread loop.
    /health's own connection() call therefore binds app.db's singleton pool
    to pytest-asyncio's session loop here. A later test module's own
    TestClient would otherwise inherit this already-open, wrong-loop-bound
    pool and fail (or, as observed, hang) the moment it tries to open/close
    it from its own portal loop. Closing here keeps this test module's pool
    usage self-contained, same reasoning as tests/test_db_pool.py's
    equivalent fixture.
    """
    yield
    from app.db import close_pool

    await close_pool()


async def test_independent_requests_do_not_serialise(pg_available):
    """Ten concurrent /health calls must overlap, not queue. Under the old
    sync handler each occupied a pool thread; under async they share the
    loop."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        start = time.perf_counter()
        results = await asyncio.gather(*(c.get("/health") for _ in range(10)))
        elapsed = time.perf_counter() - start

    assert all(r.status_code == 200 for r in results)
    assert elapsed < 2.0, f"10 concurrent health checks took {elapsed:.1f}s"
