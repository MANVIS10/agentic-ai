import asyncio
import os
import sys

import pytest

# Windows-only, must run before pytest-asyncio creates any test's event
# loop (module load time, i.e. now, is early enough - a per-test import
# inside an async test function body would not be). psycopg's async mode
# cannot run on Windows' default ProactorEventLoop ("Psycopg cannot use the
# 'ProactorEventLoop' to run in async mode"); WindowsSelectorEventLoopPolicy
# is the documented workaround. Harmless on non-Windows platforms, where
# this attribute doesn't exist and the block is skipped entirely.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def pg_available():
    """Skip a test when Postgres is unreachable.

    Phase 2 (async conversion): app.db no longer exposes a synchronous
    get_connection() to probe - its connection pool is async-only. This
    check is deliberately independent of app.db's own API: it just opens a
    plain, throwaway sync psycopg connection against the same
    settings.database_url to answer "is Postgres reachable", the same
    question the old check answered, without needing an event loop here
    (this fixture itself is sync so it can be shared by sync and async
    tests alike).
    """
    import psycopg

    from app.config import settings

    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")


@pytest.fixture(scope="session")
def openai_available():
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear the in-process rate-limit window before every test.

    The limiter's state is a module-level dict keyed by user_id and IP with
    a 60-second sliding window, and the end-to-end tests reuse one user_id
    across many rate-limited routes. Whether a given test tripped a 429
    therefore depended on how many earlier tests had run and how slow the
    real LLM calls were - making the suite intermittently red for reasons
    unrelated to the code under test. Clearing between tests makes each one
    independent of its neighbours' timing.

    This resets TEST state only; the limiter's own behaviour is unchanged
    and is still covered directly by tests/test_security.py.
    """
    from app.security.ratelimit import _rate_limit_state

    _rate_limit_state.clear()
    yield
    _rate_limit_state.clear()
