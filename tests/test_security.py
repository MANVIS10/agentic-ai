import pytest
from fastapi import HTTPException

from app.security.ratelimit import _rate_limit_state, enforce_rate_limits
from app.security.validation import validate_text_field


def test_each_scope_has_an_independent_budget():
    _rate_limit_state.clear()
    for _ in range(10):
        enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    assert exc.value.status_code == 429
    # a different scope must NOT be exhausted by chat traffic
    enforce_rate_limits("search", "u1", "1.1.1.1", (20, 60), (60, 60))


def test_blank_text_field_is_rejected():
    with pytest.raises(HTTPException):
        validate_text_field("   ", "question")
