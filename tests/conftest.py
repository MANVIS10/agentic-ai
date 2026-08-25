import os

import pytest


@pytest.fixture(scope="session")
def pg_available():
    from app.db import get_connection

    try:
        get_connection().execute("SELECT 1").fetchone()
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
