"""Bearer-token authentication.

Until now `user_id` was simply a string in the request body. Every
retrieval path filtered on it correctly - `WHERE d.user_id = %s` has been
there since Stage 23 - but nothing established that the caller *was* that
user, so the isolation was arithmetic, not a boundary. Anyone who knew or
guessed a `user_id` could read that user's documents. This module supplies
the missing half: the `user_id` the routes filter on now comes from a
signed token the server issued, never from caller-controlled input.

**What this is not.** There is no user table, no password, no registration
flow, and no revocation list. A caller presents one shared signup secret
and names the `user_id` it wants a token for. That is a bootstrap
credential, not an identity provider: it stops a stranger from reading
another user's documents, and it does not stop someone who holds the
signup secret from minting a token for any user_id they like. Ranking the
two: the first is the hole that mattered with real users' documents in the
database, and closing it needs no new infrastructure. The second wants a
real IdP, which is a much larger change than this one.

Tokens are signed with stdlib HMAC-SHA256 rather than a JWT library, for
the same reason the rate limiter is a dict rather than Redis: it is the
whole of what is needed here, it adds no dependency to pin, and it stays
readable top-to-bottom.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from fastapi import HTTPException, Request

from app.config import settings
from app.security.validation import validate_text_field

logger = logging.getLogger(__name__)

USER_SCOPE = "user"
ADMIN_SCOPE = "admin"

# Used only when auth_secret_key is unset, which validate_for_startup()
# permits in dev and refuses in prod. Regenerated per process, so a restart
# invalidates every token it issued - fine locally, useless in production,
# which is exactly why prod requires a configured key instead.
_EPHEMERAL_SECRET_KEY = secrets.token_urlsafe(32)

# The detail string every authentication failure returns, whatever actually
# went wrong. A caller learns that its token was not accepted and nothing
# else: distinguishing "expired" from "bad signature" from "wrong scope"
# tells an attacker which half of a forgery attempt was on the right track.
_AUTH_FAILED_DETAIL = "Not authenticated"


def _signing_key() -> bytes:
    return (settings.auth_secret_key.strip() or _EPHEMERAL_SECRET_KEY).encode("utf-8")


def _b64encode(raw: bytes) -> str:
    """URL-safe base64 with the padding stripped, so a token is one word
    with no characters that need escaping in a header."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def _sign(payload: bytes) -> str:
    return _b64encode(hmac.new(_signing_key(), payload, hashlib.sha256).digest())


def issue_token(subject: str, *, scope: str = USER_SCOPE, ttl_seconds: int | None = None) -> str:
    """Mint `payload.signature` for `subject`.

    `scope` separates an ordinary user's token from the one the maintenance
    route requires, so a leaked user token cannot be replayed against
    /documents/backfill-embeddings.
    """
    ttl = settings.auth_token_ttl_seconds if ttl_seconds is None else ttl_seconds
    payload = json.dumps(
        {"sub": subject, "scope": scope, "exp": int(time.time()) + ttl},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{_b64encode(payload)}.{_sign(payload)}"


def verify_token(token: str, *, expected_scope: str = USER_SCOPE) -> str:
    """Return the subject of a valid token, or raise 401.

    Verifies the signature BEFORE parsing the payload as JSON - the payload
    is attacker-supplied until the HMAC says otherwise, and json.loads is
    not something to point at unverified bytes.
    """
    try:
        encoded_payload, signature = token.split(".", 1)
        payload = _b64decode(encoded_payload)
    except (ValueError, TypeError, base64.binascii.Error):
        raise _unauthenticated()

    # compare_digest, not ==: an early-exiting comparison leaks how much of a
    # forged signature was correct, one byte at a time.
    if not hmac.compare_digest(_sign(payload), signature):
        raise _unauthenticated()

    try:
        claims = json.loads(payload)
        subject = claims["sub"]
        scope = claims["scope"]
        expires_at = int(claims["exp"])
    except (ValueError, KeyError, TypeError):
        raise _unauthenticated()

    if scope != expected_scope:
        raise _unauthenticated()
    if time.time() >= expires_at:
        raise _unauthenticated()
    if not isinstance(subject, str) or not subject.strip():
        raise _unauthenticated()

    return subject


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=_AUTH_FAILED_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthenticated()
    return token.strip()


def current_user_id(request: Request) -> str:
    """FastAPI dependency: the authenticated caller's user_id.

    Every route that used to read `user_id` from the request body, a form
    field, or a query string now depends on this instead. Those fields are
    still accepted (the frontend is migrating) but their values are ignored.

    The result goes through validate_text_field so the same empty/length
    rules every other user_id already obeyed still apply - a token is signed
    by us, but "signed by us" is not the same as "shaped the way the rest of
    the system expects".
    """
    return validate_text_field(verify_token(_bearer_token(request)), "user_id")


def require_admin(request: Request) -> str:
    """FastAPI dependency guarding the one cross-tenant maintenance route."""
    return verify_token(_bearer_token(request), expected_scope=ADMIN_SCOPE)


def verify_signup_secret(presented: str, *, expected: str, label: str) -> None:
    """Check a bootstrap secret before issuing a token.

    When `expected` is unset this permits issuance and logs a warning, so
    local development works with no setup. That is safe only because
    validate_for_startup() refuses to boot a prod process whose signup
    secret is empty - the permissive branch is unreachable in production.
    """
    if not expected.strip():
        logger.warning(
            "%s is not configured; issuing a token without checking it. "
            "This is permitted in dev only.",
            label,
        )
        return
    if not hmac.compare_digest(presented, expected):
        raise _unauthenticated()
