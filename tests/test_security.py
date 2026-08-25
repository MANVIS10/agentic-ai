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
