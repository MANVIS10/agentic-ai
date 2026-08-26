"""Every user-scoped route rejects an unauthenticated request.

Deliberately runs WITHOUT Postgres or an OpenAI key. `TestClient(app)` is
used without entering it as a context manager, so FastAPI's lifespan never
runs and no database connection is ever opened - the authentication
dependency resolves before the route body, so a rejected request never
reaches anything that would need one. That is worth asserting in its own
right: an unauthenticated caller must not be able to make this process do
work, and it also keeps this file runnable on a machine with nothing
installed but the dependencies.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import auth_headers

client = TestClient(app)

# (method, path, kwargs) for every route that must demand a token.
PROTECTED_ROUTES = [
    ("post", "/chat", {"json": {"question": "q", "thread_id": "t", "user_id": "u"}}),
    ("post", "/approve", {"json": {"thread_id": "t"}}),
    ("post", "/reject", {"json": {"thread_id": "t"}}),
    ("get", "/documents", {"params": {"user_id": "u"}}),
    ("post", "/documents/search", {"json": {"query": "q", "user_id": "u"}}),
    ("post", "/documents/backfill-embeddings", {}),
    (
        "post",
        "/documents/upload",
        {"files": {"file": ("a.txt", b"hi", "text/plain")}, "data": {"user_id": "u"}},
    ),
]


@pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES)
def test_route_rejects_a_request_with_no_token(method, path, kwargs):
    assert getattr(client, method)(path, **kwargs).status_code == 401


@pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES)
def test_route_rejects_a_forged_token(method, path, kwargs):
    headers = {"Authorization": "Bearer forged.token"}
    assert getattr(client, method)(path, headers=headers, **kwargs).status_code == 401


@pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES)
def test_a_self_asserted_user_id_is_not_enough(method, path, kwargs):
    """The exact pre-auth calling convention - a user_id in the body, form,
    or query string and no token - must no longer work anywhere."""
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_backfill_rejects_an_ordinary_user_token():
    """It is a cross-tenant maintenance route: a user token must not open
    it, even a perfectly valid one."""
    response = client.post("/documents/backfill-embeddings", headers=auth_headers("alice"))
    assert response.status_code == 401


def test_health_is_not_behind_authentication():
    """Monitoring must not need a credential (spec §9).

    Asserted against the route table rather than by calling /health, on
    purpose: calling it would open the connection pool, whose background
    reconnect workers then keep this process alive long after the tests
    finish on a machine with no database. /health's actual behavior is
    covered where Postgres is available.
    """
    from app.api.routers.health import router as health_router

    health_route = next(r for r in health_router.routes if r.path == "/health")
    assert health_route.dependant.dependencies == [], (
        "/health must stay callable without a token"
    )
