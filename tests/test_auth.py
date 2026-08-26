"""Bearer-token authentication.

Until this phase `user_id` was a string in the request body. The retrieval
filters were always right; nothing established that the caller was the user
it named. These checks pin down the boundary itself - forging, tampering,
expiry, scope confusion - without needing Postgres or the OpenAI API.
"""

import time

import pytest
from fastapi import HTTPException, Request

from app.security import auth


def _request_with(header: str | None) -> Request:
    """A bare ASGI scope is enough - none of these paths read a body."""
    headers = [] if header is None else [(b"authorization", header.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_a_token_round_trips():
    token = auth.issue_token("alice")
    assert auth.verify_token(token) == "alice"


def test_current_user_id_reads_the_bearer_header():
    token = auth.issue_token("alice")
    assert auth.current_user_id(_request_with(f"Bearer {token}")) == "alice"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "alice",  # the pre-auth world: a bare user_id
        "Basic YWxpY2U6cHc=",
        "Bearer ",
        "Bearer not-a-token",
        "Bearer a.b.c",
    ],
)
def test_a_request_without_a_valid_bearer_token_is_rejected(header):
    with pytest.raises(HTTPException) as excinfo:
        auth.current_user_id(_request_with(header))
    assert excinfo.value.status_code == 401


def test_a_tampered_payload_is_rejected():
    """The signature is what makes the user_id trustworthy - re-encoding the
    payload to name a different user must not survive it."""
    import json

    token = auth.issue_token("alice")
    _, signature = token.split(".", 1)
    forged_payload = auth._b64encode(
        json.dumps({"sub": "bob", "scope": "user", "exp": int(time.time()) + 600}).encode()
    )
    with pytest.raises(HTTPException) as excinfo:
        auth.verify_token(f"{forged_payload}.{signature}")
    assert excinfo.value.status_code == 401


def test_a_token_signed_with_another_key_is_rejected(monkeypatch):
    monkeypatch.setattr(auth, "_signing_key", lambda: b"attacker-key")
    forged = auth.issue_token("alice")
    monkeypatch.undo()
    with pytest.raises(HTTPException):
        auth.verify_token(forged)


def test_an_expired_token_is_rejected():
    with pytest.raises(HTTPException):
        auth.verify_token(auth.issue_token("alice", ttl_seconds=-1))


def test_a_user_token_is_not_an_admin_token():
    """Scope confusion is the whole reason /documents/backfill-embeddings
    takes a different secret: an ordinary user's token must not open the
    cross-tenant maintenance route."""
    user_token = auth.issue_token("alice", scope=auth.USER_SCOPE)
    with pytest.raises(HTTPException):
        auth.require_admin(_request_with(f"Bearer {user_token}"))

    admin_token = auth.issue_token("ops", scope=auth.ADMIN_SCOPE)
    assert auth.require_admin(_request_with(f"Bearer {admin_token}")) == "ops"


def test_an_admin_token_does_not_authenticate_as_a_user():
    """The reverse direction too - scopes are not a ladder."""
    admin_token = auth.issue_token("ops", scope=auth.ADMIN_SCOPE)
    with pytest.raises(HTTPException):
        auth.current_user_id(_request_with(f"Bearer {admin_token}"))


def test_failures_do_not_say_which_check_failed():
    """Distinguishing 'expired' from 'bad signature' tells an attacker which
    half of a forgery attempt was on the right track."""
    details = set()
    for token in ["garbage", auth.issue_token("a", ttl_seconds=-1)]:
        with pytest.raises(HTTPException) as excinfo:
            auth.verify_token(token)
        details.add(excinfo.value.detail)
    assert len(details) == 1


def test_signup_secret_is_enforced_when_configured():
    with pytest.raises(HTTPException):
        auth.verify_signup_secret("wrong", expected="right", label="TEST_SECRET")
    auth.verify_signup_secret("right", expected="right", label="TEST_SECRET")  # must not raise


def test_signup_secret_is_permissive_only_when_unconfigured():
    """Safe solely because validate_for_startup() refuses to boot a prod
    process with an empty signup secret."""
    auth.verify_signup_secret("anything", expected="", label="TEST_SECRET")
