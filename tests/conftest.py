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
