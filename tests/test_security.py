import asyncio

import pytest
from fastapi import HTTPException

from app.security.ratelimit import _rate_limit_state, enforce_rate_limits
from app.security.validation import validate_text_field


async def test_each_scope_has_an_independent_budget():
    """enforce_rate_limits is now async (Phase 2, Task 4) - awaited here
    instead of called synchronously."""
    _rate_limit_state.clear()
    for _ in range(10):
        await enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    assert exc.value.status_code == 429
    # a different scope must NOT be exhausted by chat traffic
    await enforce_rate_limits("search", "u1", "1.1.1.1", (20, 60), (60, 60))


async def test_rate_limit_window_slides():
    """A limiter that never forgets is a lockout, not a rate limit.

    Asserted here against the limiter directly rather than over HTTP: the
    integration test in test_security_guardrails.py used to check this with
    a 2-second window and real /documents/search calls, but each of those
    makes an embedding API request (~0.6-1s), so the window drained while
    the test was still filling it and the check raced itself. With no
    network in the way, a fraction-of-a-second window is exact.
    """
    _rate_limit_state.clear()
    limit = (1, 0.3)  # one request per 300ms

    await enforce_rate_limits("slide", "u1", "9.9.9.9", limit, (1000, 0.3))
    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limits("slide", "u1", "9.9.9.9", limit, (1000, 0.3))
    assert exc.value.status_code == 429

    await asyncio.sleep(0.35)  # let the window slide past the first call
    await enforce_rate_limits("slide", "u1", "9.9.9.9", limit, (1000, 0.3))


def test_blank_text_field_is_rejected():
    with pytest.raises(HTTPException):
        validate_text_field("   ", "question")


async def test_same_thread_id_serialises_but_different_ids_do_not():
    from app.security.locks import thread_lock

    order = []

    async def worker(tid, tag):
        async with thread_lock(tid):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.05)
            order.append(f"{tag}-out")

    await asyncio.gather(worker("a", "a1"), worker("a", "a2"), worker("b", "b1"))
    # the two "a" workers must not interleave with each other
    a = [x for x in order if x.startswith("a")]
    assert a in (
        ["a1-in", "a1-out", "a2-in", "a2-out"],
        ["a2-in", "a2-out", "a1-in", "a1-out"],
    )


async def test_busy_thread_raises_409_not_forever():
    from app.security import locks

    async with locks.thread_lock("busy"):
        with pytest.raises(HTTPException) as exc:
            async with locks.thread_lock("busy", timeout=0.05):
                pass
    assert exc.value.status_code == 409
