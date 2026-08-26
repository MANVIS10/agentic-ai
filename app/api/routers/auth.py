"""POST /auth/token - exchange a shared bootstrap secret for a bearer token.

New in this phase. Every other user_id-scoped route now derives its
`user_id` from the token this route issues, instead of believing whatever
the request body claimed. See app/security/auth.py's docstring for what
that does and does not buy.
"""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import AUTH_IP_RATE_LIMIT, MAX_TEXT_INPUT_LENGTH, settings
from app.security.auth import ADMIN_SCOPE, USER_SCOPE, issue_token, verify_signup_secret
from app.security.ratelimit import enforce_rate_limits
from app.security.validation import validate_text_field

router = APIRouter()


class TokenRequest(BaseModel):
    user_id: str
    secret: str
    # An admin token is only good for /documents/backfill-embeddings, and is
    # checked against a different secret - see Settings.auth_admin_secret.
    scope: Literal["user", "admin"] = USER_SCOPE


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


@router.post("/auth/token", response_model=TokenResponse)
async def create_token(request: TokenRequest, http_request: Request):
    """Issue a bearer token for `user_id`.

    Rate-limited per client IP and nothing else: there is no authenticated
    identity at this point, which is the entire purpose of the route, so the
    IP is the only dimension available. The budget is deliberately the
    tightest in the app - brute-forcing the shared secret is the whole
    threat model here, and every other route's limiter sits behind an
    identity this one hands out.
    """
    user_id = validate_text_field(request.user_id, "user_id", max_length=MAX_TEXT_INPUT_LENGTH)

    client_ip = http_request.client.host if http_request.client else "unknown"
    # Same key on both dimensions: enforce_rate_limits wants a user_id, and
    # using the *claimed* one here would let a caller reset its own budget by
    # naming a different user on each attempt.
    await enforce_rate_limits("auth", client_ip, client_ip, AUTH_IP_RATE_LIMIT, AUTH_IP_RATE_LIMIT)

    if request.scope == ADMIN_SCOPE:
        verify_signup_secret(
            request.secret, expected=settings.auth_admin_secret, label="AUTH_ADMIN_SECRET"
        )
    else:
        verify_signup_secret(
            request.secret, expected=settings.auth_signup_secret, label="AUTH_SIGNUP_SECRET"
        )

    return TokenResponse(
        access_token=issue_token(user_id, scope=request.scope),
        expires_in=settings.auth_token_ttl_seconds,
    )
